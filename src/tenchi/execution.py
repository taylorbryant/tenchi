"""Run use cases with Tenchi's guarantees from any entrypoint.

HTTP is one caller of a use case, not its owner. Workers, scripts,
schedulers, and tests invoke the same plain async functions; this module
gives those entrypoints the guarantees the HTTP server already
provides — validated input at the boundary and a scoped context whose
exit sees success or failure — without wrapping the use case in
anything::

    from tenchi.execution import execute

    await execute(notify_member_added, request_json=payload, context=ctx)

Input is validated against the ``request`` parameter's own type
annotation. Outside HTTP there is no wire metadata to declare — no
status, path, or media type — so a separate declaration object would
only duplicate what the signature already states.

Error handling stays with the entrypoint: ``AppError`` and unexpected
exceptions propagate (after flowing through a scoped context's
``__aexit__``), because how a failure is surfaced — dead-letter, exit
code, HTTP status — is the caller's decision, not the runner's.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, cast, get_type_hints

from pydantic import TypeAdapter


class _Unset:
    def __repr__(self) -> str:  # stable in signatures and API snapshots
        return "UNSET"


_UNSET = _Unset()

_adapters: dict[Any, TypeAdapter[Any]] = {}


@asynccontextmanager
async def open_context(source: Any) -> AsyncGenerator[Any]:
    """Resolve a context source into a live context.

    ``source`` may be a ready context value, a zero-argument factory, an
    async factory, or a factory returning an async context manager — the
    same contract ``create_app(context_factory=...)`` accepts. A context
    manager is entered here and exited when the block ends, with any
    exception flowing through ``__aexit__`` first, so commit-on-success /
    rollback-on-error units of work behave identically at every
    entrypoint. Callables are treated as factories.
    """
    value = source() if callable(source) else source
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, AbstractAsyncContextManager):
        scoped = cast(AbstractAsyncContextManager[Any], value)
        async with scoped as entered:
            yield entered
    else:
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
    Validation happens before the context opens, so invalid input never
    starts a unit of work. Validation failures raise pydantic's
    ``ValidationError`` for the entrypoint to translate.

    ``context`` follows :func:`open_context` semantics.
    """
    parameters = inspect.signature(use_case).parameters
    if "context" not in parameters:
        raise TypeError(
            f"execute({_describe(use_case)}): use case must accept a 'context' argument"
        )
    if request is not _UNSET and request_json is not None:
        raise TypeError(
            f"execute({_describe(use_case)}): pass request= or request_json=, not both"
        )

    kwargs: dict[str, Any] = {}
    if "request" in parameters:
        if request is _UNSET and request_json is None:
            raise TypeError(
                f"execute({_describe(use_case)}): use case declares a "
                "'request' parameter; pass request= or request_json="
            )
        annotation = get_type_hints(use_case).get("request")
        if annotation is None:
            raise TypeError(
                f"execute({_describe(use_case)}): the 'request' parameter "
                "must be annotated so input can be validated"
            )
        adapter = _adapter(annotation)
        if request_json is not None:
            kwargs["request"] = adapter.validate_json(request_json)
        else:
            kwargs["request"] = adapter.validate_python(request)
    elif request is not _UNSET or request_json is not None:
        raise TypeError(
            f"execute({_describe(use_case)}): use case does not declare a "
            "'request' parameter; the input would be silently dropped"
        )

    async with open_context(context) as entered:
        return await use_case(**kwargs, context=entered)


def _adapter(annotation: Any) -> TypeAdapter[Any]:
    adapter = _adapters.get(annotation)
    if adapter is None:
        adapter = TypeAdapter(annotation)
        _adapters[annotation] = adapter
    return adapter


def _describe(use_case: object) -> str:
    name = getattr(use_case, "__qualname__", None) or repr(use_case)
    module = getattr(use_case, "__module__", None)
    return f"{module}.{name}" if module else str(name)
