"""ASGI application assembly.

:func:`create_app` turns a route group and a context factory into a
Starlette application. Per request the server validates path parameters and
the JSON body against the contract, builds a fresh application context,
invokes the bound use case with keyword arguments, validates the result, and
serializes the response.

Expected errors — ``AppError`` instances whose definition the contract
declares — map to their declared HTTP status. Everything else (validation
failures, undeclared ``AppError``, unexpected exceptions, unmatched routes)
is framework-owned and marked with the ``x-tenchi-error-source: framework``
response header, so clients and tests can always tell the two apart.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast, overload

from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route as StarletteRoute

from . import errors as tenchi_errors
from .contracts import Contract
from .errors import ERROR_SOURCE_HEADER, AppError, ErrorDef, error_body
from .routes import Route, RouteGroup

logger = logging.getLogger("tenchi.server")

ContextFactory = Callable[..., Any]
Lifespan = Callable[[], AbstractAsyncContextManager[Any]]

_UNSET = object()


@dataclass(frozen=True, slots=True)
class RequestInfo:
    """What a hook may inspect about the incoming request.

    ``headers`` keys are lowercased HTTP names (``x-api-key``). ``contract``
    is the matched contract, so hooks can exempt routes via contract
    metadata such as ``tags``.
    """

    method: str
    path: str
    headers: Mapping[str, str]
    contract: Contract[Any]


Hook = Callable[[RequestInfo, Any], Any]
"""An application hook: ``(request_info, context) -> None | new_context``.

Hooks run in order after the context is created and before inputs are
validated. A hook authenticates or rejects at the HTTP boundary: raise
:class:`~tenchi.errors.AppError` to reject (declare the error on contracts,
typically via ``route_group(..., errors=...)``), or return a new context —
usually ``dataclasses.replace(context, user=...)`` — to attach identity.
Returning ``None`` keeps the current context. Hooks may be sync or async.
"""


class _LifespanState:
    """Holds the value yielded by the app lifespan while it is running."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: Any = _UNSET


@dataclass(frozen=True, slots=True)
class _BoundRoute:
    route: Route
    params_adapter: TypeAdapter[Any] | None
    query_adapter: TypeAdapter[Any] | None
    headers_adapter: TypeAdapter[Any] | None
    request_adapter: TypeAdapter[Any] | None
    response_adapter: TypeAdapter[Any] | None


@overload
def create_app(
    *,
    routes: RouteGroup,
    context_factory: Callable[[], object],
    lifespan: Callable[[], AbstractAsyncContextManager[object]] | None = None,
    hooks: Sequence[Hook] = (),
) -> Starlette: ...


@overload
def create_app[StateT](
    *,
    routes: RouteGroup,
    context_factory: Callable[[StateT], object],
    lifespan: Callable[[], AbstractAsyncContextManager[StateT]],
    hooks: Sequence[Hook] = (),
) -> Starlette: ...


def create_app(
    *,
    routes: RouteGroup,
    context_factory: ContextFactory,
    lifespan: Lifespan | None = None,
    hooks: Sequence[Hook] = (),
) -> Starlette:
    """Build an ASGI application from bound routes and a context factory.

    ``context_factory`` is called once per request, so the context it
    returns is request-scoped. It may also return an async context
    manager (typically an ``@asynccontextmanager`` function) — then it is
    entered at request start and exited at request end, and a hook or
    use-case exception flows through ``__aexit__`` before being mapped to
    a response, so ``async with connection.transaction():``-style
    commit-on-success / rollback-on-error resources compose naturally.

    ``lifespan`` owns process-scoped resources: an async context manager
    factory entered at startup and exited at shutdown. Whatever it yields —
    a connection, a repository, a dataclass of ports — is passed to
    ``context_factory`` on every request when the factory accepts one
    argument. A zero-argument factory may still be combined with a lifespan
    that only opens and closes module-scoped resources.

    ``hooks`` run on every request after the context is created and before
    inputs are validated; see :data:`Hook`. Authentication belongs here;
    business authorization belongs in use cases.
    """
    takes_state = _context_factory_takes_state(context_factory)
    if takes_state and lifespan is None:
        raise ValueError(
            "create_app: context_factory accepts a lifespan state argument "
            "but no lifespan= was provided"
        )

    state = _LifespanState()
    starlette_routes: list[StarletteRoute] = []
    seen: set[tuple[str, str]] = set()

    for item in routes:
        key = (item.contract.method, item.contract.path)
        if key in seen:
            raise ValueError(f"create_app: duplicate route {key[0]} {key[1]}")
        seen.add(key)

        bound = _BoundRoute(
            route=item,
            params_adapter=(
                TypeAdapter(item.contract.params)
                if item.contract.params is not None
                else None
            ),
            query_adapter=(
                TypeAdapter(item.contract.query)
                if item.contract.query is not None
                else None
            ),
            headers_adapter=(
                TypeAdapter(item.contract.headers)
                if item.contract.headers is not None
                else None
            ),
            request_adapter=(
                TypeAdapter(item.contract.request)
                if item.contract.request is not None
                else None
            ),
            response_adapter=(
                TypeAdapter(item.contract.response)
                if item.contract.response is not None
                else None
            ),
        )
        starlette_routes.append(
            StarletteRoute(
                item.contract.path,
                _make_endpoint(
                    bound, context_factory, takes_state, state, tuple(hooks)
                ),
                methods=[item.contract.method],
                name=None,
            )
        )

    return Starlette(
        routes=starlette_routes,
        lifespan=_starlette_lifespan(lifespan, state) if lifespan else None,
        exception_handlers={
            HTTPException: _handle_http_exception,
            Exception: _handle_unexpected_exception,
        },
    )


def _context_factory_takes_state(context_factory: ContextFactory) -> bool:
    try:
        signature = inspect.signature(context_factory)
    except (TypeError, ValueError):
        return False

    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        and parameter.default is inspect.Parameter.empty
    ]
    if len(required) > 1:
        raise ValueError(
            "create_app: context_factory must take zero arguments or a "
            f"single lifespan state argument, not {len(required)}"
        )
    return len(required) == 1


def _starlette_lifespan(
    lifespan: Lifespan, state: _LifespanState
) -> Callable[[Starlette], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def run(_: Starlette) -> AsyncGenerator[None]:
        async with lifespan() as value:
            state.value = value
            try:
                yield
            finally:
                state.value = _UNSET

    return run


def _header_dict(request: Request) -> dict[str, str]:
    """Request headers keyed by lowercased HTTP name; repeats keep the last."""
    return {key.lower(): value for key, value in request.headers.items()}


def _header_fields(request: Request) -> dict[str, str]:
    """Request headers keyed by Python field name (``x-api-key`` →
    ``x_api_key``) for validation against a headers model."""
    return {
        key.lower().replace("-", "_"): value for key, value in request.headers.items()
    }


def _query_dict(query_params: QueryParams) -> dict[str, str | list[str]]:
    """Collapse a query multi-dict: single values stay scalar, repeats list."""
    raw: dict[str, str | list[str]] = {}
    for key, value in query_params.multi_items():
        existing = raw.get(key)
        if existing is None:
            raw[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            raw[key] = [existing, value]
    return raw


def _make_endpoint(
    bound: _BoundRoute,
    context_factory: ContextFactory,
    takes_state: bool,
    state: _LifespanState,
    hooks: tuple[Hook, ...],
) -> Callable[[Request], Awaitable[Response]]:
    contract = bound.route.contract
    use_case = bound.route.use_case

    def build_context() -> Any:
        if not takes_state:
            return context_factory()
        if state.value is _UNSET:
            raise RuntimeError(
                f"{contract.name}: context_factory expects lifespan state, "
                "but the lifespan has not run. Serve the app with lifespan "
                "support (uvicorn does this by default; in tests wrap the "
                "app in asgi-lifespan's LifespanManager)."
            )
        return context_factory(state.value)

    async def dispatch(request: Request, context: Any) -> Response:
        """Run hooks, validation, and the use case for one request.

        ``AppError`` and unexpected exceptions propagate to the caller so
        they pass through a request-scoped context manager's ``__aexit__``
        (rolling back a transaction) before being mapped to a response.
        Validation failures return early: no application work has run, so
        a request-scoped context exits cleanly.
        """
        # Hooks run before input validation, so an authentication
        # rejection wins over a validation error.
        if hooks:
            info = RequestInfo(
                method=request.method,
                path=request.url.path,
                headers=_header_dict(request),
                contract=contract,
            )
            for hook in hooks:
                outcome = hook(info, context)
                if inspect.isawaitable(outcome):
                    outcome = await outcome
                if outcome is not None:
                    context = outcome

        kwargs: dict[str, Any] = {"context": context}
        try:
            if bound.params_adapter is not None:
                kwargs["params"] = bound.params_adapter.validate_python(
                    request.path_params
                )
            if bound.query_adapter is not None:
                kwargs["query"] = bound.query_adapter.validate_python(
                    _query_dict(request.query_params)
                )
            if bound.headers_adapter is not None:
                kwargs["headers"] = bound.headers_adapter.validate_python(
                    _header_fields(request)
                )
            if bound.request_adapter is not None:
                body = await request.body()
                if contract.request_media_type == "application/json":
                    kwargs["request"] = bound.request_adapter.validate_json(body)
                elif contract.request_media_type.startswith("text/"):
                    kwargs["request"] = bound.request_adapter.validate_python(
                        body.decode("utf-8")
                    )
                else:
                    kwargs["request"] = bound.request_adapter.validate_python(body)
        except ValidationError as exc:
            return _framework_error_response(
                tenchi_errors.validation_error,
                details=exc.errors(include_url=False, include_input=False),
            )
        except UnicodeDecodeError:
            return _framework_error_response(
                tenchi_errors.validation_error,
                details=[{"msg": "Request body is not valid UTF-8 text"}],
            )

        result = await use_case(**kwargs)

        if bound.response_adapter is None:
            return Response(status_code=contract.status)

        try:
            validated = bound.response_adapter.validate_python(result)
            if contract.response_media_type == "application/json":
                payload: bytes | str = bound.response_adapter.dump_json(validated)
            elif isinstance(validated, bytes | str):
                payload = validated
            else:
                payload = bound.response_adapter.dump_json(validated)
        except ValidationError:
            logger.exception(
                "Response from %s does not match the contract's response type",
                contract.name,
            )
            return _framework_error_response(tenchi_errors.internal_server_error)

        return Response(
            payload,
            status_code=contract.status,
            media_type=contract.response_media_type,
        )

    async def endpoint(request: Request) -> Response:
        try:
            raw = build_context()
            if inspect.isawaitable(raw):
                raw = await raw
            if isinstance(raw, AbstractAsyncContextManager):
                # Request-scoped context: entered per request; a use-case
                # or hook exception flows through __aexit__ (rolling back
                # a transaction) before being mapped below.
                scoped = cast(AbstractAsyncContextManager[Any], raw)
                async with scoped as context:
                    return await dispatch(request, context)
            return await dispatch(request, raw)
        except AppError as exc:
            if contract.declares_error(exc.definition):
                return _app_error_response(exc)
            logger.exception(
                "Undeclared AppError %r raised handling %s; declare it on "
                "the contract's errors (or route_group(errors=...)) to "
                "expose it",
                exc.code,
                contract.name,
            )
            return _framework_error_response(tenchi_errors.internal_server_error)
        except Exception:
            logger.exception("Unhandled exception in %s", contract.name)
            return _framework_error_response(tenchi_errors.internal_server_error)

    return endpoint


def _app_error_response(exc: AppError) -> JSONResponse:
    return JSONResponse(
        error_body(code=exc.code, message=exc.message, details=exc.details),
        status_code=exc.status,
        headers={**exc.headers, ERROR_SOURCE_HEADER: "app"},
    )


def _framework_error_response(
    definition: ErrorDef, *, details: Any = None
) -> JSONResponse:
    return JSONResponse(
        error_body(code=definition.code, message=definition.message, details=details),
        status_code=definition.status,
        headers={ERROR_SOURCE_HEADER: "framework"},
    )


def _handle_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    if exc.status_code == 404:
        return _framework_error_response(tenchi_errors.not_found)
    if exc.status_code == 405:
        return _framework_error_response(tenchi_errors.method_not_allowed)
    return JSONResponse(
        error_body(code=f"HTTP_{exc.status_code}", message=exc.detail),
        status_code=exc.status_code,
        headers={ERROR_SOURCE_HEADER: "framework"},
    )


def _handle_unexpected_exception(request: Request, exc: Exception) -> Response:
    logger.exception("Unhandled exception outside route dispatch")
    return _framework_error_response(tenchi_errors.internal_server_error)
