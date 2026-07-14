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
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, overload
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route as StarletteRoute

from . import errors as tenchi_errors
from .contracts import Contract
from .errors import (
    ERROR_SOURCE_HEADER,
    REQUEST_ID_HEADER,
    AppError,
    ErrorDef,
    error_body,
)
from .routes import Route, RouteGroup

logger = logging.getLogger("tenchi.server")

ContextFactory = Callable[..., Any]
Lifespan = Callable[[], AbstractAsyncContextManager[Any]]

_UNSET = object()


class _ResponseContractViolation(Exception):
    """Raised when a use-case result fails response validation.

    Raised (not returned) so it propagates through a request-scoped
    context manager's ``__aexit__`` — the request's transaction must roll
    back, exactly as for any other internal error, before the 500 is
    built.
    """


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
    request_id: str
    """Correlation id: the inbound ``x-request-id`` or a generated one.
    Echoed on every response header and in error envelopes."""


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
    query_sequence_fields: frozenset[str]


@overload
def create_app(
    *,
    routes: RouteGroup,
    context_factory: Callable[[], object],
    lifespan: Callable[[], AbstractAsyncContextManager[object]] | None = None,
    hooks: Sequence[Hook] = (),
    middleware: Sequence[Middleware] = (),
) -> Starlette: ...


@overload
def create_app[StateT](
    *,
    routes: RouteGroup,
    context_factory: Callable[[StateT], object],
    lifespan: Callable[[], AbstractAsyncContextManager[StateT]],
    hooks: Sequence[Hook] = (),
    middleware: Sequence[Middleware] = (),
) -> Starlette: ...


def create_app(
    *,
    routes: RouteGroup,
    context_factory: ContextFactory,
    lifespan: Lifespan | None = None,
    hooks: Sequence[Hook] = (),
    middleware: Sequence[Middleware] = (),
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

    ``middleware`` is passed straight to Starlette — the seam for CORS,
    compression, and other ASGI concerns::

        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware

        create_app(..., middleware=[Middleware(CORSMiddleware,
                                               allow_origins=["https://app.example.com"])])
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
            query_sequence_fields=_sequence_query_fields(item.contract.query),
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
        middleware=list(middleware),
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


def _request_id(request: Request) -> str:
    """The inbound ``x-request-id`` when reasonable, else a generated id."""
    inbound = request.headers.get(REQUEST_ID_HEADER, "")
    if 0 < len(inbound) <= 200:
        return inbound
    return uuid4().hex


def _header_dict(request: Request) -> dict[str, str]:
    """Request headers keyed by lowercased HTTP name; repeats keep the last."""
    return {key.lower(): value for key, value in request.headers.items()}


def _header_fields(request: Request) -> dict[str, str]:
    """Request headers keyed by Python field name (``x-api-key`` →
    ``x_api_key``) for validation against a headers model."""
    return {
        key.lower().replace("-", "_"): value for key, value in request.headers.items()
    }


def _query_dict(
    query_params: QueryParams, sequence_fields: frozenset[str]
) -> dict[str, str | list[str]]:
    """Collapse a query multi-dict: single values stay scalar, repeats list.

    Keys the query model declares as sequences are always lists, so
    ``?tags=a`` validates against ``tags: list[str]`` the same way
    ``?tags=a&tags=b`` does.
    """
    raw: dict[str, str | list[str]] = {}
    for key, value in query_params.multi_items():
        existing = raw.get(key)
        if existing is None:
            raw[key] = [value] if key in sequence_fields else value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            raw[key] = [existing, value]
    return raw


_SEQUENCE_ORIGINS = (list, set, frozenset, tuple)


def _sequence_query_fields(annotation: Any) -> frozenset[str]:
    """Field names of a query model whose annotations expect a sequence."""
    if not (inspect.isclass(annotation) and issubclass(annotation, BaseModel)):
        return frozenset()
    return frozenset(
        name
        for name, field in annotation.model_fields.items()
        if _expects_sequence(field.annotation)
    )


def _expects_sequence(annotation: Any) -> bool:
    if annotation in _SEQUENCE_ORIGINS:
        return True
    origin = get_origin(annotation)
    if origin in _SEQUENCE_ORIGINS:
        return True
    if origin is Union or origin is UnionType:
        return any(
            _expects_sequence(argument)
            for argument in get_args(annotation)
            if argument is not type(None)
        )
    return False


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

    async def dispatch(request: Request, context: Any, request_id: str) -> Response:
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
                request_id=request_id,
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
                    _query_dict(request.query_params, bound.query_sequence_fields)
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
            # include_context=False: a custom validator's ctx holds the
            # live exception object, which is not JSON-serializable.
            return _framework_error_response(
                tenchi_errors.validation_error,
                details=exc.errors(
                    include_url=False, include_input=False, include_context=False
                ),
                request_id=request_id,
            )
        except UnicodeDecodeError:
            return _framework_error_response(
                tenchi_errors.validation_error,
                details=[{"msg": "Request body is not valid UTF-8 text"}],
                request_id=request_id,
            )

        result = await use_case(**kwargs)

        if bound.response_adapter is None:
            return Response(
                status_code=contract.status,
                headers={REQUEST_ID_HEADER: request_id},
            )

        try:
            validated = bound.response_adapter.validate_python(result)
            if contract.response_media_type == "application/json":
                payload: bytes | str = bound.response_adapter.dump_json(validated)
            elif isinstance(validated, bytes | str):
                payload = validated
            else:
                payload = bound.response_adapter.dump_json(validated)
        except ValidationError as exc:
            logger.exception(
                "Response from %s does not match the contract's response "
                "type [request_id=%s]",
                contract.name,
                request_id,
            )
            # Raise, not return: the request scope must roll back — the
            # use case's writes must not commit behind a 500.
            raise _ResponseContractViolation from exc

        return Response(
            payload,
            status_code=contract.status,
            media_type=contract.response_media_type,
            headers={REQUEST_ID_HEADER: request_id},
        )

    async def endpoint(request: Request) -> Response:
        request_id = _request_id(request)
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
                    return await dispatch(request, context, request_id)
            return await dispatch(request, raw, request_id)
        except AppError as exc:
            if contract.declares_error(exc.definition):
                return _app_error_response(exc, request_id=request_id)
            logger.exception(
                "Undeclared AppError %r raised handling %s; declare it on "
                "the contract's errors (or route_group(errors=...)) to "
                "expose it [request_id=%s]",
                exc.code,
                contract.name,
                request_id,
            )
            return _framework_error_response(
                tenchi_errors.internal_server_error, request_id=request_id
            )
        except _ResponseContractViolation:
            # Already logged where it was detected; the raise existed only
            # to route the failure through the request scope's __aexit__.
            return _framework_error_response(
                tenchi_errors.internal_server_error, request_id=request_id
            )
        except Exception:
            logger.exception(
                "Unhandled exception in %s [request_id=%s]",
                contract.name,
                request_id,
            )
            return _framework_error_response(
                tenchi_errors.internal_server_error, request_id=request_id
            )

    return endpoint


def _app_error_response(exc: AppError, *, request_id: str) -> JSONResponse:
    return JSONResponse(
        error_body(
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=request_id,
        ),
        status_code=exc.status,
        headers={
            **exc.headers,
            ERROR_SOURCE_HEADER: "app",
            REQUEST_ID_HEADER: request_id,
        },
    )


def _framework_error_response(
    definition: ErrorDef,
    *,
    details: Any = None,
    request_id: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    headers = {**(extra_headers or {}), ERROR_SOURCE_HEADER: "framework"}
    if request_id is not None:
        headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        error_body(
            code=definition.code,
            message=definition.message,
            details=details,
            request_id=request_id,
        ),
        status_code=definition.status,
        headers=headers,
    )


def _handle_http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)
    request_id = _request_id(request)
    # Preserve headers the exception carries (Starlette's 405 sets Allow;
    # middleware may set WWW-Authenticate); framework headers win.
    extra = dict(exc.headers or {})
    if exc.status_code == 404:
        return _framework_error_response(
            tenchi_errors.not_found, request_id=request_id, extra_headers=extra
        )
    if exc.status_code == 405:
        return _framework_error_response(
            tenchi_errors.method_not_allowed,
            request_id=request_id,
            extra_headers=extra,
        )
    return JSONResponse(
        error_body(
            code=f"HTTP_{exc.status_code}",
            message=exc.detail,
            request_id=request_id,
        ),
        status_code=exc.status_code,
        headers={
            **extra,
            ERROR_SOURCE_HEADER: "framework",
            REQUEST_ID_HEADER: request_id,
        },
    )


def _handle_unexpected_exception(request: Request, exc: Exception) -> Response:
    request_id = _request_id(request)
    logger.exception(
        "Unhandled exception outside route dispatch [request_id=%s]", request_id
    )
    return _framework_error_response(
        tenchi_errors.internal_server_error, request_id=request_id
    )
