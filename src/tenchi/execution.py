"""Run use cases with Tenchi's guarantees from any entrypoint.

HTTP is one caller of a use case, not its owner. Workers, scripts,
schedulers, and tests invoke the same plain async functions; this module
gives those entrypoints the boundary guarantees the HTTP server
provides — validated input and a scoped context whose exit sees success
or failure — without wrapping the use case in anything::

    from tenchi.execution import execute

    await execute(notify_member_added, request_json=payload, context=ctx)

Input is validated against the ``request`` parameter's own type
annotation. Outside HTTP there is no wire metadata to declare — no
status, path, or media type — so a separate declaration object would
only duplicate what the signature already states.

Failure taxonomy, so entrypoints can react per class:

- :class:`ExecutionError` — the call is miswired (missing parameters,
  unresolvable annotation, unusable context source). Deterministic;
  retrying cannot help. Raised before the context opens.
- ``pydantic.ValidationError`` — the input does not match the declared
  type. Also deterministic, also raised before the context opens.
- Everything the use case itself raises — ``AppError`` and unexpected
  exceptions — propagates after flowing through a scoped context's
  ``__aexit__``, because how a failure is surfaced (dead-letter, exit
  code, HTTP status) is the caller's decision, not the runner's.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
)
from typing import Any, cast

from pydantic import TypeAdapter

from .errors import TenchiError


class ExecutionError(TenchiError, TypeError):
    """The execute() call is miswired: the use case's signature, the
    input, or the context source cannot work. Deterministic — a retry
    with the same arguments fails the same way — so queue-style callers
    should dead-letter rather than retry."""


class _Unset:
    def __repr__(self) -> str:  # stable in signatures and API snapshots
        return "UNSET"


_UNSET = _Unset()

_adapters: dict[Any, TypeAdapter[Any]] = {}


@asynccontextmanager
async def open_context(source: Any) -> AsyncGenerator[Any]:
    """Resolve a context source into a live context.

    ``source`` may be a ready context value, a zero-argument factory, an
    async factory, or a factory returning an async context manager. A
    context manager is entered here and exited when the block ends, with
    any exception flowing through ``__aexit__`` first, so
    commit-on-success / rollback-on-error units of work behave
    identically at every entrypoint.

    Callables are treated as factories, so pass instances, not classes
    (and note that a factory taking the lifespan state, as
    ``create_app`` supports, must be wrapped in a closure here — there
    is no lifespan to draw state from). Factory arity is checked before
    invocation; exceptions raised inside a valid factory propagate as-is.
    Sources that cannot provide the promised scoping — sync context
    managers, bare async generators — are rejected rather than silently
    passed through unscoped.
    """
    value = _context_value(source)
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, AbstractAsyncContextManager):
        scoped = cast(AbstractAsyncContextManager[Any], value)
        async with scoped as entered:
            yield entered
        return
    if isinstance(value, AbstractContextManager):
        raise ExecutionError(
            "open_context: got a sync context manager; a request-scoped "
            "context must be async (@asynccontextmanager)"
        )
    if inspect.isasyncgen(value):
        raise ExecutionError(
            "open_context: got a bare async generator; decorate the "
            "factory with @asynccontextmanager"
        )
    yield value


async def execute[ResultT](
    use_case: Callable[..., Awaitable[ResultT]],
    *,
    context: Any,
    request: Any = _UNSET,
    request_json: bytes | str | None = None,
) -> ResultT:
    """Invoke ``use_case`` with validated input and a scoped context.

    ``request`` is Python data (or an already-typed value) validated
    against the use case's ``request`` annotation; ``request_json`` is a
    raw JSON payload validated the same way — pass one or the other.
    The signature is checked and the input validated before the context
    opens, so neither a miswired call nor invalid input ever starts a
    unit of work. Miswiring raises :class:`ExecutionError`; invalid
    input raises pydantic's ``ValidationError``.

    ``context`` follows :func:`open_context` semantics.
    """
    kwargs = _validated_kwargs(use_case, request, request_json)
    async with open_context(context) as entered:
        return await use_case(**kwargs, context=entered)


def _validated_kwargs(
    use_case: Callable[..., Any], request: Any, request_json: bytes | str | None
) -> dict[str, Any]:
    """Check the signature eagerly (the same rules route() applies) and
    validate the input, so every failure here precedes the context."""
    if not inspect.iscoroutinefunction(use_case):
        raise ExecutionError(
            f"execute({_describe(use_case)}): use case must be an async function"
        )

    try:
        parameters = inspect.signature(use_case).parameters
    except (TypeError, ValueError) as exc:
        raise ExecutionError(
            f"execute({_describe(use_case)}): could not inspect the use "
            f"case's signature: {exc}"
        ) from exc

    accepts_any_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    for name in ("request", "context"):
        parameter = parameters.get(name)
        if parameter is not None and parameter.kind not in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            raise ExecutionError(
                f"execute({_describe(use_case)}): parameter {name!r} must "
                "be addressable by keyword"
            )
    if "context" not in parameters and not accepts_any_kwargs:
        raise ExecutionError(
            f"execute({_describe(use_case)}): use case must accept a 'context' argument"
        )
    for parameter in parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        if (
            parameter.name not in ("request", "context")
            and parameter.default is inspect.Parameter.empty
        ):
            raise ExecutionError(
                f"execute({_describe(use_case)}): use case has required "
                f"parameter {parameter.name!r} that execute() does not "
                "provide; it only passes 'request' and 'context'"
            )

    if request is not _UNSET and request_json is not None:
        raise ExecutionError(
            f"execute({_describe(use_case)}): pass request= or request_json=, not both"
        )

    supplied = request is not _UNSET or request_json is not None
    declared = parameters.get("request")

    if declared is None:
        if supplied:
            target = (
                "a '**kwargs' parameter, which cannot be validated"
                if accepts_any_kwargs
                else "no 'request' parameter; the input would be dropped"
            )
            raise ExecutionError(
                f"execute({_describe(use_case)}): use case declares "
                f"{target}; declare an annotated 'request' parameter"
            )
        return {}

    if not supplied:
        if declared.default is not inspect.Parameter.empty:
            return {}  # the signature's own default applies
        raise ExecutionError(
            f"execute({_describe(use_case)}): use case declares a "
            "'request' parameter; pass request= or request_json="
        )

    annotation = _request_annotation(use_case, declared)
    try:
        adapter = _adapter(annotation)
    except Exception as exc:
        raise ExecutionError(
            f"execute({_describe(use_case)}): Pydantic cannot validate request "
            f"annotation {_type_name(annotation)}: {exc}"
        ) from exc
    if request_json is not None:
        return {"request": adapter.validate_json(request_json)}
    return {"request": adapter.validate_python(request)}


def _request_annotation(use_case: Callable[..., Any], declared: Any) -> Any:
    """Resolve only the ``request`` annotation.

    ``typing.get_type_hints`` would resolve every annotation in the
    signature — and fail on ``TYPE_CHECKING``-only context types, an
    idiom the layering rules themselves encourage. Only the request
    annotation matters here, so only it is evaluated.
    """
    annotation = declared.annotation
    if annotation is inspect.Parameter.empty:
        raise ExecutionError(
            f"execute({_describe(use_case)}): the 'request' parameter "
            "must be annotated so input can be validated"
        )
    function: Any = use_case
    while isinstance(function, functools.partial):
        function = function.func
    function = inspect.unwrap(function)
    namespace = getattr(function, "__globals__", {})
    original = annotation
    seen: set[str] = set()
    while isinstance(annotation, str):
        if annotation in seen:
            raise ExecutionError(
                f"execute({_describe(use_case)}): could not resolve the "
                f"'request' annotation {original!r}: cyclic forward reference"
            )
        seen.add(annotation)
        try:
            annotation = eval(annotation, namespace)
        except Exception as exc:
            raise ExecutionError(
                f"execute({_describe(use_case)}): could not resolve the "
                f"'request' annotation {original!r}: {exc}"
            ) from exc
    return annotation


def _adapter(annotation: Any) -> TypeAdapter[Any]:
    try:
        cached = _adapters.get(annotation)
    except TypeError:  # unhashable annotation: skip the cache
        return TypeAdapter(annotation)
    if cached is None:
        cached = TypeAdapter(annotation)
        _adapters[annotation] = cached
    return cached


def _type_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", repr(annotation))


def _context_value(source: Any) -> Any:
    """Return a ready context or invoke a valid zero-argument factory.

    Checking the call shape before invocation keeps a missing lifespan-state
    argument in the deterministic ``ExecutionError`` category without
    misclassifying a real ``TypeError`` raised inside a valid factory.
    """
    if not callable(source):
        return source
    if inspect.isclass(source):
        raise ExecutionError(
            f"open_context({_describe(source)}): pass a context instance, not a class"
        )
    try:
        signature = inspect.signature(source)
    except (TypeError, ValueError) as exc:
        raise ExecutionError(
            f"open_context({_describe(source)}): could not inspect the context "
            f"factory's signature: {exc}"
        ) from exc
    try:
        signature.bind()
    except TypeError as exc:
        raise ExecutionError(
            f"open_context({_describe(source)}): context factory must accept zero "
            f"arguments: {exc}"
        ) from exc
    return source()


def _describe(use_case: object) -> str:
    name = getattr(use_case, "__qualname__", None) or repr(use_case)
    module = getattr(use_case, "__module__", None)
    return f"{module}.{name}" if module else str(name)
