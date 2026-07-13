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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route as StarletteRoute

from . import errors as tenchi_errors
from .errors import ERROR_SOURCE_HEADER, AppError, ErrorDef, error_body
from .routes import Route, RouteGroup

logger = logging.getLogger("tenchi.server")

ContextFactory = Callable[[], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class _BoundRoute:
    route: Route
    params_adapter: TypeAdapter[Any] | None
    query_adapter: TypeAdapter[Any] | None
    request_adapter: TypeAdapter[Any] | None
    response_adapter: TypeAdapter[Any] | None


def create_app(
    *,
    routes: RouteGroup,
    context_factory: ContextFactory,
) -> Starlette:
    """Build an ASGI application from bound routes and a context factory.

    ``context_factory`` is called once per request, so the context it
    returns is request-scoped; long-lived resources such as repositories
    should be created at module scope in server composition and closed over
    by the factory.
    """
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
                _make_endpoint(bound, context_factory),
                methods=[item.contract.method],
                name=None,
            )
        )

    return Starlette(
        routes=starlette_routes,
        exception_handlers={
            HTTPException: _handle_http_exception,
            Exception: _handle_unexpected_exception,
        },
    )


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
) -> Callable[[Request], Awaitable[Response]]:
    contract = bound.route.contract
    use_case = bound.route.use_case

    async def endpoint(request: Request) -> Response:
        kwargs: dict[str, Any] = {}

        try:
            if bound.params_adapter is not None:
                kwargs["params"] = bound.params_adapter.validate_python(
                    request.path_params
                )
            if bound.query_adapter is not None:
                kwargs["query"] = bound.query_adapter.validate_python(
                    _query_dict(request.query_params)
                )
            if bound.request_adapter is not None:
                kwargs["request"] = bound.request_adapter.validate_json(
                    await request.body()
                )
        except ValidationError as exc:
            return _framework_error_response(
                tenchi_errors.validation_error,
                details=exc.errors(include_url=False, include_input=False),
            )

        try:
            context = context_factory()
            if inspect.isawaitable(context):
                context = await context
            kwargs["context"] = context

            result = await use_case(**kwargs)
        except AppError as exc:
            if contract.declares_error(exc.definition):
                return _app_error_response(exc)
            logger.exception(
                "Undeclared AppError %r raised by %s; declare it on the "
                "contract's errors to expose it",
                exc.code,
                contract.name,
            )
            return _framework_error_response(tenchi_errors.internal_server_error)
        except Exception:
            logger.exception("Unhandled exception in %s", contract.name)
            return _framework_error_response(tenchi_errors.internal_server_error)

        if bound.response_adapter is None:
            return Response(status_code=contract.status)

        try:
            validated = bound.response_adapter.validate_python(result)
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
            media_type="application/json",
        )

    return endpoint


def _app_error_response(exc: AppError) -> JSONResponse:
    return JSONResponse(
        error_body(code=exc.code, message=exc.message, details=exc.details),
        status_code=exc.status,
        headers={ERROR_SOURCE_HEADER: "app"},
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
