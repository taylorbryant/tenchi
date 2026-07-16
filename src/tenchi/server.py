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
from datetime import UTC, datetime
from email.utils import format_datetime
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, overload
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route as StarletteRoute

from . import errors as tenchi_errors
from .contracts import (
    Contract,
    _is_json_media_type,  # pyright: ignore[reportPrivateUsage]
    _is_text_media_type,  # pyright: ignore[reportPrivateUsage]
    _object_schema,  # pyright: ignore[reportPrivateUsage]
    _render_response_header_value,  # pyright: ignore[reportPrivateUsage]
    _response_header_fields,  # pyright: ignore[reportPrivateUsage]
)
from .errors import (
    ERROR_SOURCE_HEADER,
    REQUEST_ID_HEADER,
    AppError,
    ConfigurationError,
    ErrorDef,
    error_body,
)
from .execution import open_context
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


class _BodyTooLarge(Exception):
    """The request body exceeded the route's byte ceiling."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"request body exceeds {limit} bytes")
        self.limit = limit


DEFAULT_MAX_REQUEST_BYTES = 1_048_576  # 1 MiB
"""App-wide request body ceiling unless ``create_app(max_request_bytes=...)``
or a contract's ``max_request_bytes`` says otherwise."""


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
    contract: Contract[Any, Any]
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
    response_headers_adapter: TypeAdapter[Any] | None
    response_header_names: frozenset[str]
    required_response_headers: frozenset[str]
    query_sequence_fields: frozenset[str]
    header_fields: tuple[str, ...]
    body_limit: int | None
    lifecycle_headers: tuple[tuple[str, str], ...]


@overload
def create_app(
    *,
    routes: RouteGroup,
    context_factory: Callable[[], object],
    lifespan: Callable[[], AbstractAsyncContextManager[object]] | None = None,
    hooks: Sequence[Hook] = (),
    middleware: Sequence[Middleware] = (),
    max_request_bytes: int | None = DEFAULT_MAX_REQUEST_BYTES,
) -> Starlette: ...


@overload
def create_app[StateT](
    *,
    routes: RouteGroup,
    context_factory: Callable[[StateT], object],
    lifespan: Callable[[], AbstractAsyncContextManager[StateT]],
    hooks: Sequence[Hook] = (),
    middleware: Sequence[Middleware] = (),
    max_request_bytes: int | None = DEFAULT_MAX_REQUEST_BYTES,
) -> Starlette: ...


def create_app(
    *,
    routes: RouteGroup,
    context_factory: ContextFactory,
    lifespan: Lifespan | None = None,
    hooks: Sequence[Hook] = (),
    middleware: Sequence[Middleware] = (),
    max_request_bytes: int | None = DEFAULT_MAX_REQUEST_BYTES,
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

    ``max_request_bytes`` caps request body size app-wide (default 1
    MiB); bodies over the cap are rejected with the framework's 413
    before validation. A contract's own ``max_request_bytes`` overrides
    the app default per route; pass ``None`` here to disable the app
    default entirely (per-contract ceilings still apply).
    """
    _validate_body_limit(max_request_bytes)
    takes_state = _context_factory_takes_state(context_factory)
    if lifespan is not None:
        _require_call_shape(
            lifespan,
            positional_arguments=0,
            label="create_app: lifespan",
            expectation="accept zero arguments",
        )
    hook_chain = tuple(hooks)
    for index, hook in enumerate(hook_chain):
        _require_call_shape(
            hook,
            positional_arguments=2,
            label=f"create_app: hook[{index}]",
            expectation="accept two positional arguments (request_info, context)",
        )
    if takes_state and lifespan is None:
        raise ConfigurationError(
            "create_app: context_factory accepts a lifespan state argument "
            "but no lifespan= was provided"
        )
    state = _LifespanState()
    starlette_routes: list[StarletteRoute] = []
    seen: set[tuple[str, str]] = set()

    for item in routes:
        key = (item.contract.method, item.contract.path)
        if key in seen:
            raise ConfigurationError(f"create_app: duplicate route {key[0]} {key[1]}")
        seen.add(key)

        params_adapter = _contract_adapter(item.contract, "params")
        query_adapter = _contract_adapter(item.contract, "query")
        headers_adapter = _contract_adapter(item.contract, "headers")
        request_adapter = _contract_adapter(item.contract, "request")
        response_adapter = _contract_adapter(item.contract, "response")
        response_headers_adapter = _contract_adapter(item.contract, "response_headers")
        response_header_names, required_response_headers = _response_header_name_sets(
            item.contract, response_headers_adapter
        )
        bound = _BoundRoute(
            route=item,
            params_adapter=params_adapter,
            query_adapter=query_adapter,
            headers_adapter=headers_adapter,
            request_adapter=request_adapter,
            response_adapter=response_adapter,
            response_headers_adapter=response_headers_adapter,
            response_header_names=response_header_names,
            required_response_headers=required_response_headers,
            query_sequence_fields=_sequence_query_fields(item.contract.query),
            header_fields=_header_field_names(headers_adapter),
            body_limit=(
                item.contract.max_request_bytes
                if item.contract.max_request_bytes is not None
                else max_request_bytes
            ),
            lifecycle_headers=_lifecycle_headers(item.contract),
        )
        try:
            starlette_route = StarletteRoute(
                item.contract.path,
                _make_endpoint(bound, context_factory, takes_state, state, hook_chain),
                methods=[item.contract.method],
                name=None,
            )
        except (AssertionError, ValueError) as exc:
            raise ConfigurationError(
                f"create_app: route {item.contract.name!r} has invalid path "
                f"{item.contract.path!r}: {exc}"
            ) from exc
        starlette_routes.append(starlette_route)

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
    signature = _callable_signature(
        context_factory, label="create_app: context_factory"
    )
    try:
        signature.bind()
    except TypeError:
        pass
    else:
        return False

    try:
        signature.bind(object())
    except TypeError as exc:
        raise ConfigurationError(
            "create_app: context_factory must accept zero arguments or a single "
            f"positional lifespan state argument: {exc}"
        ) from exc
    return True


def _validate_body_limit(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigurationError(
            "create_app: max_request_bytes must be positive (a non-bool int, or "
            f"None to disable the app-wide cap), got {value!r}"
        )


def _callable_signature(value: object, *, label: str) -> inspect.Signature:
    try:
        return inspect.signature(cast(Callable[..., object], value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{label} has no inspectable signature: {exc}"
        ) from exc


def _require_call_shape(
    value: object,
    *,
    positional_arguments: int,
    label: str,
    expectation: str,
) -> None:
    signature = _callable_signature(value, label=label)
    try:
        signature.bind(*(object() for _ in range(positional_arguments)))
    except TypeError as exc:
        raise ConfigurationError(f"{label} must {expectation}: {exc}") from exc


def _contract_adapter(
    contract: Contract[Any, Any], slot: str
) -> TypeAdapter[Any] | None:
    annotation = getattr(contract, slot)
    if annotation is None:
        return None
    type_name = getattr(annotation, "__name__", repr(annotation))
    try:
        adapter = TypeAdapter(annotation)
        if not adapter.pydantic_complete:
            adapter.rebuild(raise_errors=True)
        if not adapter.pydantic_complete:
            raise TypeError("adapter remains incomplete after rebuilding")
    except Exception as exc:
        raise ConfigurationError(
            f"create_app: route {contract.name!r} has a {slot} type {type_name} "
            f"Pydantic cannot validate: {exc}"
        ) from exc
    if slot in {"params", "query", "headers", "response_headers"}:
        mode = "serialization" if slot == "response_headers" else "validation"
        try:
            schema = adapter.json_schema(mode=mode, by_alias=True)
            validation_schema = (
                adapter.json_schema(mode="validation", by_alias=True)
                if slot == "response_headers"
                else None
            )
        except Exception as exc:
            raise ConfigurationError(
                f"create_app: route {contract.name!r} has a {slot} type "
                f"{type_name} Pydantic cannot describe: {exc}"
            ) from exc
        if slot == "response_headers":
            _response_header_fields(
                schema,
                label=(
                    f"create_app: route {contract.name!r} response_headers type "
                    f"{type_name}"
                ),
                validation_schema=validation_schema,
            )
        elif _object_schema(schema) is None:
            raise ConfigurationError(
                f"create_app: route {contract.name!r} has a {slot} type "
                f"{type_name} that must describe object-shaped input"
            )
    return adapter


def _response_header_name_sets(
    contract: Contract[Any, Any], adapter: TypeAdapter[Any] | None
) -> tuple[frozenset[str], frozenset[str]]:
    if adapter is None:
        return frozenset(), frozenset()
    fields = _response_header_fields(
        adapter.json_schema(mode="serialization", by_alias=True),
        label=f"create_app: route {contract.name!r} response_headers",
    )
    names = frozenset(wire_name.casefold() for _, wire_name, _, _ in fields)
    required = frozenset(
        wire_name.casefold() for _, wire_name, _, is_required in fields if is_required
    )
    return names, required


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


def _header_fields(request: Request, fields: tuple[str, ...]) -> dict[str, str]:
    """Select declared headers under their exact Pydantic validation names.

    Header lookup is case-insensitive, and underscores in Python-style names
    map to hyphens on the wire. Keeping the exact declared key lets aliases
    such as ``Field(alias="X-API-Key")`` validate correctly.
    """
    selected: dict[str, str] = {}
    for field in fields:
        values = request.headers.getlist(field.replace("_", "-"))
        if values:
            selected[field] = values[-1]
    return selected


def _lifecycle_headers(
    contract: Contract[Any, Any],
) -> tuple[tuple[str, str], ...]:
    """Static response headers a contract's lifecycle metadata implies."""
    headers: list[tuple[str, str]] = []
    if isinstance(contract.deprecated, datetime):
        # RFC 9745: a structured-field Date of when deprecation applied.
        headers.append(("deprecation", f"@{int(contract.deprecated.timestamp())}"))
    elif contract.deprecated:
        headers.append(("deprecation", "true"))  # pre-RFC legacy form
    if contract.sunset is not None:
        as_utc = contract.sunset.astimezone(UTC)
        headers.append(("sunset", format_datetime(as_utc, usegmt=True)))
    return tuple(headers)


def _declared_content_length(value: str | None) -> int | None:
    """Parse a Content-Length declaration defensively.

    ``isdecimal`` + ``isascii`` rejects Unicode digit lookalikes that
    ``int()`` refuses, and the length cap avoids CPython's int-digit
    limit — a malformed declaration must fall back to counted-stream
    enforcement, never crash into a 500.
    """
    if value is None or len(value) > 19 or not value.isascii():
        return None
    if not value.isdecimal():
        return None
    return int(value)


async def _read_body(request: Request, limit: int | None) -> bytes:
    """Read the request body, enforcing the route's byte ceiling.

    The declared ``Content-Length`` is checked first so oversized
    uploads are refused without reading them; the stream is counted as
    well because the declaration may be absent (chunked) or dishonest.
    """
    if limit is None:
        return await request.body()

    declared = _declared_content_length(request.headers.get("content-length"))
    if declared is not None and declared > limit:
        raise _BodyTooLarge(limit)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _BodyTooLarge(limit)
        chunks.append(chunk)
    return b"".join(chunks)


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
    """Wire names of query fields whose annotations expect a sequence."""
    if not (inspect.isclass(annotation) and issubclass(annotation, BaseModel)):
        return frozenset()
    return frozenset(
        field.validation_alias if isinstance(field.validation_alias, str) else name
        for name, field in annotation.model_fields.items()
        if _expects_sequence(field.annotation)
    )


def _header_field_names(adapter: TypeAdapter[Any] | None) -> tuple[str, ...]:
    if adapter is None:
        return ()
    schema = _object_schema(adapter.json_schema(mode="validation", by_alias=True))
    if schema is None:
        return ()  # The adapter was already checked during composition.
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return ()
    return tuple(cast(Mapping[str, Any], properties))


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
                    _header_fields(request, bound.header_fields)
                )
            if bound.request_adapter is not None:
                body = await _read_body(request, bound.body_limit)
                if _is_json_media_type(contract.request_media_type):
                    kwargs["request"] = bound.request_adapter.validate_json(body)
                elif _is_text_media_type(contract.request_media_type):
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
        except _BodyTooLarge as exc:
            # Like a validation failure: no application work has run, so
            # a request-scoped context exits cleanly.
            return _framework_error_response(
                tenchi_errors.request_too_large,
                details={"limit_bytes": exc.limit},
                request_id=request_id,
            )

        result = await use_case(**kwargs)
        payload: bytes | str | None = None
        validated: Any = None

        if bound.response_adapter is not None:
            try:
                validated = bound.response_adapter.validate_python(result)
                if _is_json_media_type(contract.response_media_type):
                    payload = bound.response_adapter.dump_json(validated, by_alias=True)
                elif isinstance(validated, bytes | str):
                    payload = validated
                else:
                    logger.error(
                        "Response from %s validated as %s but cannot be encoded as %s; "
                        "non-JSON response media types require str or bytes "
                        "[request_id=%s]",
                        contract.name,
                        type(validated).__name__,
                        contract.response_media_type,
                        request_id,
                    )
                    raise _ResponseContractViolation
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

        response_headers: dict[str, str] = {REQUEST_ID_HEADER: request_id}
        if bound.response_headers_adapter is not None:
            projector = bound.route.response_headers
            assert projector is not None  # route() checked the declaration
            try:
                projected = projector(validated)
                validated_headers = bound.response_headers_adapter.validate_python(
                    projected
                )
                dumped = bound.response_headers_adapter.dump_python(
                    validated_headers,
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
                if not isinstance(dumped, Mapping):
                    raise ValueError("response headers must serialize to a mapping")
                for raw_name, raw_value in cast(
                    Mapping[object, object], dumped
                ).items():
                    if not isinstance(raw_name, str):
                        raise ValueError("response header names must be strings")
                    name = raw_name.replace("_", "-")
                    if name.casefold() not in bound.response_header_names:
                        raise ValueError(
                            f"response header {name!r} was not declared by the "
                            "contract's response_headers type"
                        )
                    response_headers[name] = _render_response_header_value(
                        name,
                        raw_value,
                        label=f"route {contract.name!r}",
                    )
                emitted = {name.casefold() for name in response_headers}
                missing = bound.required_response_headers - emitted
                if missing:
                    raise ValueError(
                        f"required response headers were omitted: {sorted(missing)}"
                    )
            except Exception as exc:
                logger.exception(
                    "Response headers from %s do not match the contract "
                    "[request_id=%s]",
                    contract.name,
                    request_id,
                )
                raise _ResponseContractViolation from exc

        if payload is None:
            return Response(status_code=contract.status, headers=response_headers)

        return Response(
            payload,
            status_code=contract.status,
            media_type=contract.response_media_type,
            headers=response_headers,
        )

    async def endpoint(request: Request) -> Response:
        response = await respond(request)
        # Lifecycle headers accompany every response from this route —
        # success and error alike — so deprecation is visible however
        # the call went.
        for key, value in bound.lifecycle_headers:
            if key not in response.headers:
                response.headers[key] = value
        return response

    async def respond(request: Request) -> Response:
        request_id = _request_id(request)
        try:
            # open_context handles plain values, async factories, and
            # request-scoped context managers (a use-case or hook
            # exception flows through __aexit__ — rolling back a
            # transaction — before being mapped below). Scoping matches
            # tenchi.execution.execute exactly; ordering deliberately
            # does not: HTTP opens the scope first because hooks need a
            # context before validation, so a validation failure here
            # exits the scope cleanly, while execute() validates before
            # any scope exists.
            async with open_context(build_context) as context:
                return await dispatch(request, context, request_id)
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
        except ClientDisconnect:
            # The client went away mid-request (commonly an abandoned
            # upload). Nothing to deliver and nobody to deliver it to —
            # this is routine traffic, not an application error, so no
            # error-level log. 499 is the conventional code for it; the
            # response is never actually sent.
            logger.info(
                "Client disconnected during %s [request_id=%s]",
                contract.name,
                request_id,
            )
            return Response(status_code=499)
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
