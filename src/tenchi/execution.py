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
import logging
from asyncio import CancelledError
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
)
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, cast

from pydantic import TypeAdapter

from .errors import AppError, TenchiError

logger = logging.getLogger("tenchi.execution")


class ExecutionError(TenchiError, TypeError):
    """The execute() call is miswired: the use case's signature, the
    input, or the context source cannot work. Deterministic — a retry
    with the same arguments fails the same way — so queue-style callers
    should dead-letter rather than retry."""


@dataclass(frozen=True, slots=True)
class UseCaseOutcome:
    """The finalized result of one invoked use case.

    The outcome is delivered after its surrounding context scope has closed.
    ``duration_seconds`` measures the use-case call itself; it deliberately
    excludes input validation, context acquisition and cleanup, HTTP response
    presentation, and observer work. ``completed_at`` is captured when the
    function returns or raises, before scope cleanup and observer delivery.
    """

    use_case: Callable[..., Awaitable[Any]]
    entrypoint: Literal["http", "execute", "job", "task", "tool"]
    status: Literal["succeeded", "app_error", "failed", "cancelled"]
    duration_seconds: float
    error_code: str | None = None
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))


UseCaseObserver = Callable[[UseCaseOutcome], Any]
"""A sync or async observer of finalized use-case outcomes.

Observers run in declaration order after the surrounding context scope closes.
Their failures are logged and isolated from later observers and the use-case
caller.
"""


class _UseCaseCall:
    """Capture one invocation now so observers can run after scope cleanup."""

    __slots__ = ("entrypoint", "outcome", "use_case")

    use_case: Callable[..., Awaitable[Any]]
    entrypoint: Literal["http", "execute", "job", "task", "tool"]
    outcome: UseCaseOutcome | None

    def __init__(
        self,
        use_case: Callable[..., Awaitable[Any]],
        *,
        entrypoint: Literal["http", "execute", "job", "task", "tool"],
    ) -> None:
        self.use_case = use_case
        self.entrypoint = entrypoint
        self.outcome: UseCaseOutcome | None = None

    async def invoke(self, **kwargs: Any) -> Any:
        started = perf_counter()
        try:
            result = await self.use_case(**kwargs)
        except CancelledError:
            self._finish("cancelled", started=started)
            raise
        except AppError as exc:
            self._finish("app_error", started=started, error_code=exc.code)
            raise
        except BaseException:
            self._finish("failed", started=started)
            raise
        self._finish("succeeded", started=started)
        return result

    def _finish(
        self,
        status: Literal["succeeded", "app_error", "failed", "cancelled"],
        *,
        started: float,
        error_code: str | None = None,
    ) -> None:
        self.outcome = UseCaseOutcome(
            use_case=self.use_case,
            entrypoint=self.entrypoint,
            status=status,
            duration_seconds=perf_counter() - started,
            error_code=error_code,
            completed_at=datetime.now(UTC),
        )


async def _notify_use_case_observers(
    observers: tuple[UseCaseObserver, ...],
    call: _UseCaseCall,
) -> None:
    outcome = call.outcome
    if outcome is None:
        return
    for index, observer in enumerate(observers):
        try:
            result = observer(outcome)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Use-case observer[%d] failed for %s",
                index,
                _describe(outcome.use_case),
            )


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
    use_case_observers: Sequence[UseCaseObserver] = (),
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

    ``use_case_observers`` receive one immutable :class:`UseCaseOutcome` after
    the context closes, but only when the use case was actually invoked. Sync
    and async observers run in order; failures are logged and do not alter the
    result or exception.
    """
    kwargs = _validated_kwargs(use_case, request, request_json)
    observer_chain = _validated_observers(use_case_observers)
    if not observer_chain:
        async with open_context(context) as entered:
            return await use_case(**kwargs, context=entered)

    call = _UseCaseCall(use_case, entrypoint="execute")
    try:
        async with open_context(context) as entered:
            return cast(ResultT, await call.invoke(**kwargs, context=entered))
    finally:
        await _notify_use_case_observers(observer_chain, call)


def _validated_observers(
    observers: Sequence[UseCaseObserver],
) -> tuple[UseCaseObserver, ...]:
    chain = tuple(observers)
    for index, observer in enumerate(chain):
        try:
            signature = inspect.signature(observer)
            signature.bind(object())
        except (TypeError, ValueError) as exc:
            raise ExecutionError(
                f"execute: use_case_observers[{index}] must accept one "
                f"positional UseCaseOutcome argument: {exc}"
            ) from exc
    return chain


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
