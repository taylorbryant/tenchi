"""Contract declarations for the HTTP boundary.

A contract is pure data: method, path, the types validated at the boundary,
the successful response status, and the application errors the route is
allowed to return. Validation itself happens in the server, which builds Pydantic
``TypeAdapter`` instances from these declarations, so any type Pydantic can
validate works as a request, params, or response type — models, dataclasses,
``list[Model]``, and so on.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from types import UnionType
from typing import Any, Generic, Protocol, Union, cast, get_args, get_origin, overload

from typing_extensions import TypeVar

from ._media_types import MediaTypeError, validate_media_type
from .errors import (
    ConfigurationError,
    ErrorDef,
    _validated_error_defs,  # pyright: ignore[reportPrivateUsage]
)
from .responses import (
    ResponseDef,
    _validate_example_items,  # pyright: ignore[reportPrivateUsage]
    _validated_examples,  # pyright: ignore[reportPrivateUsage]
    _validated_response_defs,  # pyright: ignore[reportPrivateUsage]
)

ResponseT = TypeVar("ResponseT", default=Any)
ResponseHeadersT = TypeVar("ResponseHeadersT", default=None)
_ResponseBodyCo = TypeVar("_ResponseBodyCo", covariant=True)
_ResponseHeadersCo = TypeVar("_ResponseHeadersCo", covariant=True)


class _ResponseDefView(Protocol[_ResponseBodyCo, _ResponseHeadersCo]):
    """Covariant view used only to infer a contract's aggregate types.

    ``ResponseDef`` itself remains invariant so ``present()`` rejects a body
    or header value of the wrong type instead of widening both arguments to a
    union. The structural view lets a heterogeneous tuple infer that union at
    contract declaration time without exposing a second public abstraction.
    """

    @property
    def body(self) -> type[_ResponseBodyCo] | UnionType | None: ...

    @property
    def headers(self) -> type[_ResponseHeadersCo] | UnionType | None: ...

    @property
    def _tenchi_response_definition(self) -> None: ...


_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_PATH_PARAMETER = re.compile(
    r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::[a-zA-Z_][a-zA-Z0-9_]*)?\}"
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "content-type",
        "deprecation",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "set-cookie",
        "sunset",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-request-id",
        "x-tenchi-error-source",
    }
)
_RESERVED_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "content-type",
        "host",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_SCALAR_HEADER_TYPES = frozenset({"string", "integer", "number", "boolean", "null"})


@dataclass(frozen=True)
class Contract(Generic[ResponseT, ResponseHeadersT]):
    """One declared HTTP operation.

    Build these with :func:`contract` rather than instantiating directly.
    """

    method: str
    path: str
    request: type[Any] | UnionType | None = None
    params: type[Any] | UnionType | None = None
    query: type[Any] | UnionType | None = None
    headers: type[Any] | UnionType | None = None
    response: type[ResponseT] | UnionType | None = None
    response_headers: type[ResponseHeadersT] | UnionType | None = None
    request_examples: tuple[tuple[str, object], ...] = ()
    response_examples: tuple[tuple[str, object], ...] = ()
    status: int = 200
    errors: tuple[ErrorDef, ...] = ()
    name: str = field(default="")
    request_media_type: str = "application/json"
    response_media_type: str = "application/json"
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    public: bool = False
    webhook: bool = False
    idempotency_key: bool = False
    deprecated: bool | datetime = False
    sunset: datetime | None = None
    max_request_bytes: int | None = None
    responses: tuple[ResponseDef[Any, Any], ...] = ()
    timeout: float | None = None
    _binding_key: object = field(
        default_factory=object,
        init=False,
        repr=False,
        compare=False,
    )
    """Identity retained when route groups amend paths or declared errors."""

    def __post_init__(self) -> None:
        _validate_no_body_status(
            path=self.path,
            response=self.response,
            status=self.status,
            has_response_definitions=bool(self.responses),
        )
        _validate_idempotency_declaration(
            method=self.method,
            path=self.path,
            headers=self.headers,
            idempotency_key=self.idempotency_key,
        )
        _validate_example_items(
            self.request_examples,
            label=f"contract(path={self.path!r}) request_examples",
        )
        _validate_example_items(
            self.response_examples,
            label=f"contract(path={self.path!r}) response_examples",
        )
        _validate_example_body_declarations(
            path=self.path,
            request=self.request,
            response=self.response,
            has_response_definitions=bool(self.responses),
            request_examples=self.request_examples,
            response_examples=self.response_examples,
        )

    def declares_error(self, definition: ErrorDef) -> bool:
        """Whether this contract declares the given error as expected."""
        return definition in self.errors


@overload
def contract(
    *,
    method: str,
    path: str,
    request: type[Any] | UnionType | None = None,
    params: type[Any] | UnionType | None = None,
    query: type[Any] | UnionType | None = None,
    headers: type[Any] | UnionType | None = None,
    response: type[ResponseT] | UnionType | None = None,
    response_headers: type[ResponseHeadersT] | UnionType | None = None,
    request_examples: Mapping[str, object] | None = None,
    response_examples: Mapping[str, object] | None = None,
    status: int = 200,
    errors: Sequence[ErrorDef] = (),
    name: str | None = None,
    request_media_type: str = "application/json",
    response_media_type: str = "application/json",
    summary: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    public: bool = False,
    webhook: bool = False,
    idempotency_key: bool = False,
    deprecated: bool | datetime = False,
    sunset: datetime | None = None,
    max_request_bytes: int | None = None,
    responses: tuple[()] = (),
    timeout: float | None = None,
) -> Contract[ResponseT, ResponseHeadersT]: ...


@overload
def contract(
    *,
    method: str,
    path: str,
    request: type[Any] | UnionType | None = None,
    params: type[Any] | UnionType | None = None,
    query: type[Any] | UnionType | None = None,
    headers: type[Any] | UnionType | None = None,
    response: None = None,
    response_headers: None = None,
    request_examples: Mapping[str, object] | None = None,
    response_examples: None = None,
    status: int = 200,
    errors: Sequence[ErrorDef] = (),
    name: str | None = None,
    request_media_type: str = "application/json",
    response_media_type: str = "application/json",
    summary: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    public: bool = False,
    webhook: bool = False,
    idempotency_key: bool = False,
    deprecated: bool | datetime = False,
    sunset: datetime | None = None,
    max_request_bytes: int | None = None,
    responses: Sequence[_ResponseDefView[ResponseT, ResponseHeadersT]],
    timeout: float | None = None,
) -> Contract[ResponseT, ResponseHeadersT]: ...


def contract(  # pyright: ignore[reportInconsistentOverload]
    *,
    method: str,
    path: str,
    request: type[Any] | UnionType | None = None,
    params: type[Any] | UnionType | None = None,
    query: type[Any] | UnionType | None = None,
    headers: type[Any] | UnionType | None = None,
    response: type[ResponseT] | UnionType | None = None,
    response_headers: type[ResponseHeadersT] | UnionType | None = None,
    request_examples: Mapping[str, object] | None = None,
    response_examples: Mapping[str, object] | None = None,
    status: int = 200,
    errors: Sequence[ErrorDef] = (),
    name: str | None = None,
    request_media_type: str = "application/json",
    response_media_type: str = "application/json",
    summary: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    public: bool = False,
    webhook: bool = False,
    idempotency_key: bool = False,
    deprecated: bool | datetime = False,
    sunset: datetime | None = None,
    max_request_bytes: int | None = None,
    responses: Sequence[ResponseDef[Any, Any]] = (),
    timeout: float | None = None,
) -> Contract[ResponseT, ResponseHeadersT]:
    """Declare an HTTP contract.

    Args:
        method: HTTP method, such as ``"POST"``.
        path: Route path. Path parameters use ``{name}`` segments, for
            example ``"/todos/{todo_id}"``.
        request: Type validated from the JSON request body. The validated
            value is passed to the use case as its ``request`` argument.
        params: Type validated from the path parameters, usually a Pydantic
            model with one field per ``{name}`` segment. Passed to the use
            case as its ``params`` argument.
        query: Type validated from the URL query string, usually a Pydantic
            model whose fields have defaults. Passed to the use case as its
            ``query`` argument. Values arrive as strings (or lists of
            strings for repeated keys) and are coerced by Pydantic.
        headers: Type validated from the request headers, passed to the use
            case as its ``headers`` argument. Underscores in field names map
            to hyphens on the wire, so ``x_api_key`` reads ``X-Api-Key``;
            Pydantic field aliases are also honored case-insensitively as
            HTTP header names. Repeated headers keep the last value.
        response: Successful wire body type for a singular response. The use
            case result is validated against it before serialization. Omit it
            when declaring ``responses=``; Tenchi derives the aggregate typed
            client body from those definitions. ``None`` means an empty body.
        response_headers: Object-shaped type describing application-owned
            headers on a singular successful response. A bound route must provide a
            pure ``response_headers=`` projector that derives this value from
            the validated use-case result. Field aliases are the wire names;
            underscores otherwise become hyphens. The type must declare a
            fixed set of fields that validate and serialize under the same
            names, and each field must serialize to one scalar value. Dynamic
            extra fields and framework-owned header names are rejected.
        request_examples: Named example request values for OpenAPI. Values are
            validated, serialized, and revalidated through ``request`` when the
            document is generated, so published examples use the real wire
            aliases and cannot drift from the receiving boundary or published
            schema.
        response_examples: Named example response values for a singular
            response. Put examples for multi-response contracts on each
            :func:`tenchi.responses.response` definition instead.
        status: Singular successful status code between 200 and 399. Defaults
            to 200. Response definitions carry their own statuses when
            ``responses=`` is used.
            Statuses 204, 205, and 304 require ``response=None``.
        errors: Application errors this route is expected to return. Expected
            errors are mapped to their HTTP status; undeclared ``AppError``
            instances are treated as internal server errors.
        name: Optional stable name, defaulting to ``"METHOD path"``.
        request_media_type: Media type of the request body. The default,
            ``application/json``, validates the body as JSON. ``text/*``
            types validate the decoded text, and any other type (such as
            ``application/octet-stream``) validates the raw bytes — pair
            them with ``request=str`` and ``request=bytes`` respectively.
            A missing or mismatched wire ``Content-Type`` receives the
            framework's 415 response before body decoding.
        response_media_type: Media type of the singular successful response. Non-JSON
            types send ``bytes`` results as-is; ``text/*`` string results use
            the declared charset (UTF-8 by default). The typed client requires
            successful responses to carry the declared type and strictly
            decodes text with its wire charset.
        summary: Short one-line summary for documentation.
        description: Longer documentation text. When omitted, OpenAPI
            generation falls back to the bound use case's docstring.
        tags: Documentation tags grouping related operations.
        public: Whether authentication hooks should treat the operation as
            public. Tenchi exposes this metadata to hooks and uses it to exempt
            the operation from global OpenAPI security; it does not perform
            authentication itself. Defaults to ``False``.
        webhook: Require an exact-body verifier binding for this contract.
            ``create_app()`` refuses to compose the route until a matching
            ``webhook(contract, verifier)`` is supplied. The verifier runs
            before request parsing and may attach a verified service identity
            to the application context.
        idempotency_key: Document that this unsafe operation implements
            idempotent replay through a required non-empty string
            ``Idempotency-Key``
            request header. Tenchi verifies that header declaration and emits
            machine-readable OpenAPI metadata; the application still owns the
            durable idempotency implementation.
        deprecated: Mark the operation as deprecated. Pass an aware
            datetime (the instant deprecation took effect) to send an
            RFC 9745 ``Deprecation: @<unix-timestamp>`` response header;
            ``True`` sends the legacy ``Deprecation: true`` form. Either
            way OpenAPI documents the operation as deprecated.
        sunset: The instant the route is scheduled for removal (an
            aware datetime). Emitted as an RFC 8594 ``Sunset`` response
            header and as ``x-sunset`` in the OpenAPI document. Implies
            nothing by itself — pair it with ``deprecated`` when the
            route is already discouraged.
        max_request_bytes: Per-route ceiling on the request body size,
            overriding ``create_app(max_request_bytes=...)`` with a
            finite ceiling (there is no per-route "unlimited"; to
            uncap everything pass ``max_request_bytes=None`` to
            ``create_app``). Bodies over the ceiling are rejected with
            the framework's 413 before validation runs. Only meaningful
            on contracts that declare a ``request`` type.
        responses: Response definitions for routes with multiple successful
            statuses or controlled Starlette response passthrough. Their body
            and header types determine the contract's aggregate typed-client
            result, so do not also pass ``response=`` or ``response_headers=``.
            A bound route supplies a typed ``present=`` function to select a
            definition. When omitted, the singular declarations above apply.
        timeout: Seconds before Tenchi cooperatively cancels the request scope,
            including context acquisition, hooks, validation, and the use case.
            Tenchi waits for cancellation cleanup before returning its framework
            504, so cleanup is never abandoned and total wall time can exceed
            this deadline when cleanup itself takes time.
    """
    _validate_runtime_options(
        method=method,
        path=path,
        status=status,
        request_media_type=request_media_type,
        response_media_type=response_media_type,
        name=name,
        summary=summary,
        description=description,
        public=public,
        webhook=webhook,
        deprecated=deprecated,
        sunset=sunset,
        max_request_bytes=max_request_bytes,
        timeout=timeout,
    )
    declared_errors = _validated_error_defs(
        errors, label=f"contract(path={path!r}) errors"
    )
    declared_tags = _validated_tags(tags, path=path)
    declared_responses = _validated_response_defs(
        responses, label=f"contract(path={path!r}) responses"
    )
    declared_request_examples = _validated_examples(
        request_examples,
        label=f"contract(path={path!r}) request_examples",
    )
    declared_response_examples = _validated_examples(
        response_examples,
        label=f"contract(path={path!r}) response_examples",
    )

    normalized_method = method.upper()
    if normalized_method not in _METHODS:
        raise ConfigurationError(
            f"contract(path={path!r}): unsupported HTTP method {method!r}"
        )
    if not path.startswith("/"):
        raise ConfigurationError(f"contract path must start with '/', got {path!r}")
    _validate_path_template(path)
    if not 200 <= status <= 399:
        raise ConfigurationError(
            f"contract(path={path!r}): status must be between 200 and 399, got {status}"
        )
    if not request_media_type.strip() or not response_media_type.strip():
        raise ConfigurationError(
            f"contract(path={path!r}): media types must be non-empty"
        )
    for label, media_type in (
        ("request_media_type", request_media_type),
        ("response_media_type", response_media_type),
    ):
        try:
            validate_media_type(media_type)
        except MediaTypeError as exc:
            raise ConfigurationError(
                f"contract(path={path!r}): {label} is invalid: {exc}"
            ) from exc
    if sunset is not None and sunset.tzinfo is None:
        raise ConfigurationError(
            f"contract(path={path!r}): sunset must be timezone-aware so the "
            "Sunset header is unambiguous"
        )
    if isinstance(deprecated, datetime) and deprecated.tzinfo is None:
        raise ConfigurationError(
            f"contract(path={path!r}): a deprecated datetime must be "
            "timezone-aware so the Deprecation header is unambiguous"
        )
    if max_request_bytes is not None and max_request_bytes <= 0:
        raise ConfigurationError(
            f"contract(path={path!r}): max_request_bytes must be positive, "
            f"got {max_request_bytes}"
        )
    if max_request_bytes is not None and request is None:
        raise ConfigurationError(
            f"contract(path={path!r}): max_request_bytes is set but the "
            "contract declares no request type; the ceiling would never "
            "apply"
        )
    if webhook and request is None:
        raise ConfigurationError(
            f"contract(path={path!r}): webhook=True requires a request type"
        )
    _validate_idempotency_declaration(
        method=normalized_method,
        path=path,
        headers=headers,
        idempotency_key=idempotency_key,
    )
    _validate_example_body_declarations(
        path=path,
        request=request,
        response=response,
        has_response_definitions=bool(declared_responses),
        request_examples=declared_request_examples,
        response_examples=declared_response_examples,
    )
    if timeout is not None and (timeout <= 0 or not isfinite(timeout)):
        raise ConfigurationError(
            f"contract(path={path!r}): timeout must be finite and positive, "
            f"got {timeout!r}"
        )
    resolved_response: type[Any] | UnionType | None = response
    resolved_response_headers: type[Any] | UnionType | None = response_headers
    if declared_responses:
        if response is not None or response_headers is not None:
            raise ConfigurationError(
                f"contract(path={path!r}): responses derive the aggregate body "
                "and headers; do not also pass response= or response_headers="
            )
        if status != 200:
            raise ConfigurationError(
                f"contract(path={path!r}): status= is only valid for a singular "
                "response; each response definition carries its own status"
            )
        if response_media_type != "application/json":
            raise ConfigurationError(
                f"contract(path={path!r}): response_media_type= is only valid "
                "for a singular response; set media_type= on each definition"
            )
        resolved_response = _aggregate_annotation(
            tuple(definition.body for definition in declared_responses)
        )
        resolved_response_headers = _aggregate_annotation(
            tuple(definition.headers for definition in declared_responses)
        )
    return Contract(
        method=normalized_method,
        path=path,
        request=request,
        params=params,
        query=query,
        headers=headers,
        response=resolved_response,
        request_examples=declared_request_examples,
        response_examples=declared_response_examples,
        response_headers=resolved_response_headers,
        status=status,
        errors=declared_errors,
        name=name or f"{normalized_method} {path}",
        request_media_type=request_media_type,
        response_media_type=response_media_type,
        summary=summary,
        description=description,
        tags=declared_tags,
        public=public,
        webhook=webhook,
        idempotency_key=idempotency_key,
        deprecated=deprecated,
        sunset=sunset,
        max_request_bytes=max_request_bytes,
        responses=declared_responses,
        timeout=timeout,
    )


def _validate_idempotency_declaration(
    *,
    method: object,
    path: object,
    headers: object,
    idempotency_key: object,
) -> None:
    if not isinstance(idempotency_key, bool):
        raise ConfigurationError(
            f"contract(path={path!r}): idempotency_key must be a bool, got "
            f"{type(idempotency_key).__name__}"
        )
    if not idempotency_key:
        return
    if method not in _METHODS - _SAFE_METHODS:
        raise ConfigurationError(
            f"contract(path={path!r}): idempotency_key=True is only valid for "
            "unsafe methods"
        )
    if headers is None:
        raise ConfigurationError(
            f"contract(path={path!r}): idempotency_key=True requires a headers "
            f"type with a required string {_IDEMPOTENCY_KEY_HEADER!r} field"
        )


def _validate_example_body_declarations(
    *,
    path: object,
    request: object,
    response: object,
    has_response_definitions: bool,
    request_examples: object,
    response_examples: object,
) -> None:
    if request_examples and request is None:
        raise ConfigurationError(
            f"contract(path={path!r}): request_examples were provided but the "
            "contract declares no request type"
        )
    if response_examples and (response is None or has_response_definitions):
        reason = (
            "the contract uses response definitions; put examples= on each "
            "response definition"
            if has_response_definitions
            else "the contract declares no response body"
        )
        raise ConfigurationError(
            f"contract(path={path!r}): response_examples were provided but {reason}"
        )


def _validate_no_body_status(
    *,
    path: str,
    response: object,
    status: object,
    has_response_definitions: bool,
) -> None:
    if (
        not has_response_definitions
        and status in (204, 205, 304)
        and response is not None
    ):
        raise ConfigurationError(
            f"contract(path={path!r}): status {status} cannot declare a response body"
        )


def _validate_runtime_options(
    *,
    method: object,
    path: object,
    status: object,
    request_media_type: object,
    response_media_type: object,
    name: object,
    summary: object,
    description: object,
    public: object,
    webhook: object,
    deprecated: object,
    sunset: object,
    max_request_bytes: object,
    timeout: object,
) -> None:
    """Frame malformed dynamically supplied options as configuration errors."""
    if not isinstance(method, str):
        raise ConfigurationError(
            f"contract(path={path!r}): method must be a string, got "
            f"{type(method).__name__}"
        )
    if not isinstance(path, str):
        raise ConfigurationError(
            f"contract: path must be a string, got {type(path).__name__}"
        )
    if not isinstance(status, int) or isinstance(status, bool):
        raise ConfigurationError(
            f"contract(path={path!r}): status must be an int, got "
            f"{type(status).__name__}"
        )
    if not isinstance(request_media_type, str) or not isinstance(
        response_media_type, str
    ):
        raise ConfigurationError(
            f"contract(path={path!r}): media types must be strings"
        )
    for label, value in (
        ("name", name),
        ("summary", summary),
        ("description", description),
    ):
        if value is not None and not isinstance(value, str):
            raise ConfigurationError(
                f"contract(path={path!r}): {label} must be a string or None, "
                f"got {type(value).__name__}"
            )
    if not isinstance(public, bool):
        raise ConfigurationError(
            f"contract(path={path!r}): public must be a bool, got "
            f"{type(public).__name__}"
        )
    if not isinstance(webhook, bool):
        raise ConfigurationError(
            f"contract(path={path!r}): webhook must be a bool, got "
            f"{type(webhook).__name__}"
        )
    if sunset is not None and not isinstance(sunset, datetime):
        raise ConfigurationError(
            f"contract(path={path!r}): sunset must be a datetime or None, got "
            f"{type(sunset).__name__}"
        )
    if not isinstance(deprecated, (bool, datetime)):
        raise ConfigurationError(
            f"contract(path={path!r}): deprecated must be a bool or datetime, got "
            f"{type(deprecated).__name__}"
        )
    if max_request_bytes is not None and (
        not isinstance(max_request_bytes, int) or isinstance(max_request_bytes, bool)
    ):
        raise ConfigurationError(
            f"contract(path={path!r}): max_request_bytes must be an int or None, "
            f"got {type(max_request_bytes).__name__}"
        )
    if timeout is not None and (
        not isinstance(timeout, int | float) or isinstance(timeout, bool)
    ):
        raise ConfigurationError(
            f"contract(path={path!r}): timeout must be a number or None, got "
            f"{type(timeout).__name__}"
        )


def _annotation_members(annotation: object) -> tuple[object, ...]:
    normalized = type(None) if annotation is None else annotation
    origin = get_origin(normalized)
    if origin is Union or origin is UnionType:
        return cast(tuple[object, ...], get_args(normalized))
    return (normalized,)


def _aggregate_annotation(
    annotations: tuple[type[Any] | UnionType | None, ...],
) -> type[Any] | UnionType | None:
    members: list[object] = []
    for annotation in annotations:
        for member in _annotation_members(annotation):
            if member not in members:
                members.append(member)
    if members == [type(None)]:
        return None
    if len(members) == 1:
        return cast(type[Any] | UnionType, members[0])
    aggregate = members[0]
    for member in members[1:]:
        aggregate = cast(Any, aggregate) | cast(Any, member)
    return cast(type[Any] | UnionType, aggregate)


def _validated_tags(value: object, *, path: str) -> tuple[str, ...]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ConfigurationError(
            f"contract(path={path!r}): tags must be a sequence of strings"
        )
    tags: list[str] = []
    for index, tag in enumerate(cast(Sequence[object], value)):
        if not isinstance(tag, str) or not tag.strip():
            raise ConfigurationError(
                f"contract(path={path!r}): tags[{index}] must be a non-empty string"
            )
        tags.append(tag)
    return tuple(tags)


def _validate_path_template(path: str) -> None:
    remainder = _PATH_PARAMETER.sub("", path)
    if "{" in remainder or "}" in remainder:
        raise ConfigurationError(
            f"contract(path={path!r}): invalid path parameter syntax; use "
            "'{name}' or Starlette's '{name:converter}' form"
        )


def _object_schema(  # pyright: ignore[reportUnusedFunction]
    schema: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Resolve a local root reference and return an object-shaped schema."""
    root = schema
    current = schema
    seen: set[str] = set()
    while True:
        if current.get("type") == "object" or "properties" in current:
            return current
        reference = current.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return None
        if reference in seen:
            return None
        seen.add(reference)
        target: object = root
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or part not in target:
                return None
            target = cast(Mapping[object, object], target)[part]
        if not isinstance(target, Mapping):
            return None
        current = cast(Mapping[str, Any], target)


def _response_header_fields(  # pyright: ignore[reportUnusedFunction]
    schema: Mapping[str, Any],
    *,
    label: str,
    reference_root: Mapping[str, Any] | None = None,
    validation_schema: Mapping[str, Any] | None = None,
) -> tuple[tuple[str, str, Mapping[str, Any], bool], ...]:
    """Validate and return single-valued response-header field metadata."""
    object_schema = _object_schema(schema)
    if object_schema is None:
        raise ConfigurationError(f"{label} must describe object-shaped headers")
    if (
        "additionalProperties" in object_schema
        and object_schema["additionalProperties"] is not False
    ):
        raise ConfigurationError(
            f"{label} must declare fixed header fields; additional properties "
            "are not supported"
        )
    properties = object_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ConfigurationError(f"{label} must describe object-shaped headers")
    if validation_schema is not None:
        validation_object = _object_schema(validation_schema)
        if (
            validation_object is not None
            and "additionalProperties" in validation_object
            and validation_object["additionalProperties"] is not False
        ):
            raise ConfigurationError(
                f"{label} must declare fixed header fields; additional properties "
                "are not supported"
            )
        validation_properties = (
            validation_object.get("properties")
            if validation_object is not None
            else None
        )
        if not isinstance(validation_properties, Mapping) or set(
            cast(Mapping[object, object], validation_properties)
        ) != set(cast(Mapping[object, object], properties)):
            raise ConfigurationError(
                f"{label} must use the same field names for validation and "
                "serialization; use Field(alias=...) for wire names and avoid "
                "computed response-header fields"
            )
    raw_required = object_schema.get("required", [])
    required: set[object] = (
        set(cast(list[object], raw_required))
        if isinstance(raw_required, list)
        else set()
    )
    fields: list[tuple[str, str, Mapping[str, Any], bool]] = []
    seen: set[str] = set()
    for raw_name, raw_schema in cast(Mapping[object, object], properties).items():
        if not isinstance(raw_name, str) or not isinstance(raw_schema, Mapping):
            raise ConfigurationError(f"{label} has an invalid header field")
        wire_name = raw_name.replace("_", "-")
        normalized = wire_name.casefold()
        if _HEADER_NAME.fullmatch(wire_name) is None:
            raise ConfigurationError(
                f"{label} header {wire_name!r} is not a valid HTTP header name"
            )
        if normalized in _RESERVED_RESPONSE_HEADERS:
            raise ConfigurationError(
                f"{label} header {wire_name!r} is reserved by the Tenchi framework"
            )
        if normalized in seen:
            raise ConfigurationError(
                f"{label} declares header {wire_name!r} more than once"
            )
        property_schema = cast(Mapping[str, Any], raw_schema)
        if not _is_scalar_header_schema(
            reference_root or schema, property_schema, seen_refs=set()
        ):
            raise ConfigurationError(
                f"{label} header {wire_name!r} must be single-valued and scalar"
            )
        seen.add(normalized)
        fields.append((raw_name, wire_name, property_schema, raw_name in required))
    return tuple(fields)


def _request_parameter_fields(  # pyright: ignore[reportUnusedFunction]
    schema: Mapping[str, Any],
    *,
    location: str,
    label: str,
    serialization_schema: Mapping[str, Any] | None = None,
    check_required_nullable: bool = True,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Validate fields that Tenchi can encode as HTTP parameters."""
    if location not in {"path", "query", "header"}:
        raise AssertionError(f"unsupported request parameter location {location!r}")
    fields = _request_parameter_fields_in_schema(
        schema,
        location=location,
        label=label,
        check_required_nullable=check_required_nullable,
    )
    if serialization_schema is not None:
        serialized_fields = _request_parameter_fields_in_schema(
            serialization_schema,
            location=location,
            label=label,
            check_required_nullable=False,
        )
        serialized_by_name = {
            _request_parameter_wire_name(location, name): field_schema
            for name, field_schema in serialized_fields
        }
        if {_request_parameter_wire_name(location, name) for name, _ in fields} != set(
            serialized_by_name
        ):
            raise ConfigurationError(
                f"{label} must use the same wire field names for validation and "
                "serialization; use Field(alias=...) for wire names and avoid "
                "computed parameter fields"
            )
        for name, field_schema in fields:
            validation_shapes = _parameter_schema_shapes(
                schema,
                field_schema,
                seen_refs=set(),
            )
            serialization_shapes = _parameter_schema_shapes(
                serialization_schema,
                serialized_by_name[_request_parameter_wire_name(location, name)],
                seen_refs=set(),
            )
            if (
                validation_shapes is None
                or serialization_shapes is None
                or validation_shapes - {"null"} != serialization_shapes - {"null"}
            ):
                raise ConfigurationError(
                    f"{label} field {name!r} must use the same scalar or array "
                    "shape for validation and serialization"
                )
    return fields


def _validate_idempotency_key_header(  # pyright: ignore[reportUnusedFunction]
    schema: Mapping[str, Any],
    *,
    label: str,
    serialization_schema: Mapping[str, Any] | None = None,
) -> None:
    """Require the conventional idempotency header in both schema modes."""
    fields = _request_parameter_fields(
        schema,
        location="header",
        label=label,
        serialization_schema=serialization_schema,
    )
    object_schema = _object_schema(schema)
    required_value: object = object_schema.get("required", []) if object_schema else []
    required: set[object] = (
        set(cast(list[object], required_value))
        if isinstance(required_value, list)
        else set()
    )
    matching = next(
        (
            (name, field_schema)
            for name, field_schema in fields
            if _request_parameter_wire_name("header", name)
            == _IDEMPOTENCY_KEY_HEADER.casefold()
        ),
        None,
    )
    valid = (
        matching is not None
        and matching[0] in required
        and _schema_json_types(schema, matching[1], seen_refs=set())
        == frozenset({"string"})
        and _schema_requires_non_empty_string(schema, matching[1], seen_refs=set())
    )
    if valid and serialization_schema is not None:
        serialized_fields = _request_parameter_fields(
            serialization_schema,
            location="header",
            label=label,
            check_required_nullable=False,
        )
        serialized = next(
            (
                field_schema
                for name, field_schema in serialized_fields
                if _request_parameter_wire_name("header", name)
                == _IDEMPOTENCY_KEY_HEADER.casefold()
            ),
            None,
        )
        valid = (
            serialized is not None
            and _schema_json_types(
                serialization_schema,
                serialized,
                seen_refs=set(),
            )
            == frozenset({"string"})
            and _schema_requires_non_empty_string(
                serialization_schema,
                serialized,
                seen_refs=set(),
            )
        )
    if not valid:
        raise ConfigurationError(
            f"{label} must declare one required, non-empty string field named "
            f"{_IDEMPOTENCY_KEY_HEADER!r}"
        )


def _schema_json_types(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> frozenset[str] | None:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in seen_refs:
            return None
        target = _local_schema_reference(root, reference)
        return (
            _schema_json_types(
                root,
                target,
                seen_refs={*seen_refs, reference},
            )
            if target is not None
            else None
        )
    for union_key in ("anyOf", "oneOf"):
        choices = schema.get(union_key)
        if not isinstance(choices, list) or not choices:
            continue
        types: set[str] = set()
        for choice in cast(list[object], choices):
            if not isinstance(choice, Mapping):
                return None
            choice_types = _schema_json_types(
                root,
                cast(Mapping[str, Any], choice),
                seen_refs=set(seen_refs),
            )
            if choice_types is None:
                return None
            types.update(choice_types)
        return frozenset(types)
    intersections = schema.get("allOf")
    if isinstance(intersections, list) and intersections:
        known: list[frozenset[str]] = []
        for choice in cast(list[object], intersections):
            if not isinstance(choice, Mapping):
                return None
            choice_types = _schema_json_types(
                root,
                cast(Mapping[str, Any], choice),
                seen_refs=set(seen_refs),
            )
            if choice_types is not None:
                known.append(choice_types)
        if not known:
            return None
        result = set(known[0])
        for choice_types in known[1:]:
            result.intersection_update(choice_types)
        return frozenset(result)

    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return frozenset({schema_type})
    if isinstance(schema_type, list) and all(
        isinstance(item, str) for item in cast(list[object], schema_type)
    ):
        return frozenset(cast(list[str], schema_type))
    if "const" in schema:
        return frozenset({_json_value_type(schema["const"])})
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return frozenset(_json_value_type(value) for value in cast(list[object], enum))
    return None


def _schema_requires_non_empty_string(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> bool:
    if _schema_directly_requires_non_empty_string(schema):
        return True
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in seen_refs:
            return False
        target = _local_schema_reference(root, reference)
        if target is None:
            return False
        if _schema_requires_non_empty_string(
            root,
            target,
            seen_refs={*seen_refs, reference},
        ):
            return True
    for union_key in ("anyOf", "oneOf"):
        choices = schema.get(union_key)
        if isinstance(choices, list) and choices:
            return all(
                isinstance(choice, Mapping)
                and _schema_requires_non_empty_string(
                    root,
                    cast(Mapping[str, Any], choice),
                    seen_refs=set(seen_refs),
                )
                for choice in cast(list[object], choices)
            )
    intersections = schema.get("allOf")
    if isinstance(intersections, list) and intersections:
        return any(
            isinstance(choice, Mapping)
            and _schema_requires_non_empty_string(
                root,
                cast(Mapping[str, Any], choice),
                seen_refs=set(seen_refs),
            )
            for choice in cast(list[object], intersections)
        )
    return False


def _schema_directly_requires_non_empty_string(schema: Mapping[str, Any]) -> bool:
    minimum = schema.get("minLength")
    if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum >= 1:
        return True
    constant = schema.get("const")
    if isinstance(constant, str):
        return bool(constant)
    enum = schema.get("enum")
    enum_values = cast(list[object], enum) if isinstance(enum, list) else []
    return bool(enum_values) and all(
        isinstance(value, str) and bool(value) for value in enum_values
    )


def _json_value_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return "unknown"


def _request_parameter_fields_in_schema(
    schema: Mapping[str, Any],
    *,
    location: str,
    label: str,
    check_required_nullable: bool = True,
) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    object_schema = _object_schema(schema)
    if object_schema is None:
        raise ConfigurationError(f"{label} must describe object-shaped input")
    if (
        "additionalProperties" in object_schema
        and object_schema["additionalProperties"] is not False
    ):
        raise ConfigurationError(
            f"{label} must declare fixed {location} fields; additional properties "
            "are not supported"
        )
    properties = object_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ConfigurationError(f"{label} must describe object-shaped input")
    raw_required = object_schema.get("required", [])
    required: set[object] = (
        set(cast(list[object], raw_required))
        if isinstance(raw_required, list)
        else set()
    )

    fields: list[tuple[str, Mapping[str, Any]]] = []
    header_names: set[str] = set()
    for raw_name, raw_schema in cast(Mapping[object, object], properties).items():
        if not isinstance(raw_name, str) or not isinstance(raw_schema, Mapping):
            raise ConfigurationError(f"{label} has an invalid {location} field")
        if location == "header":
            wire_name = raw_name.replace("_", "-")
            normalized_name = _request_parameter_wire_name(location, raw_name)
            if not _HEADER_NAME.fullmatch(wire_name):
                raise ConfigurationError(
                    f"{label} field {raw_name!r} is not a valid HTTP header name"
                )
            if normalized_name in _RESERVED_REQUEST_HEADERS:
                raise ConfigurationError(
                    f"{label} field {raw_name!r} declares transport-owned HTTP "
                    f"header {wire_name!r}"
                )
            if normalized_name in header_names:
                raise ConfigurationError(
                    f"{label} fields normalize to duplicate HTTP header name "
                    f"{wire_name!r}"
                )
            header_names.add(normalized_name)
        property_schema = cast(Mapping[str, Any], raw_schema)
        shapes = _parameter_schema_shapes(
            schema,
            property_schema,
            seen_refs=set(),
        )
        if check_required_nullable and shapes is not None and "null" in shapes:
            if location == "path":
                raise ConfigurationError(
                    f"{label} field {raw_name!r} accepts null, which cannot be "
                    "represented in a URL path"
                )
            if raw_name in required:
                raise ConfigurationError(
                    f"{label} field {raw_name!r} is required but accepts null; "
                    f"nullable {location} fields must have a default so None can "
                    "be represented by omission"
                )
        has_scalar = shapes is not None and "scalar" in shapes
        has_sequence = shapes is not None and "sequence" in shapes
        supported = (
            shapes is not None
            and shapes <= {"scalar", "sequence", "null"}
            and (has_scalar != has_sequence)
            and (location == "query" or (has_scalar and "sequence" not in shapes))
        )
        if not supported:
            expected = (
                "a scalar or an array of scalar values"
                if location == "query"
                else (
                    "one non-null scalar value"
                    if location == "path"
                    else "one scalar value"
                )
            )
            raise ConfigurationError(
                f"{label} field {raw_name!r} must serialize as {expected}"
            )
        fields.append((raw_name, property_schema))
    return tuple(fields)


def _request_parameter_wire_name(location: str, name: str) -> str:
    return name.replace("_", "-").casefold() if location == "header" else name


def _parameter_schema_shapes(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> frozenset[str] | None:
    """Classify the HTTP wire shapes allowed by a parameter schema."""
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in seen_refs:
            return None
        target = _local_schema_reference(root, reference)
        return (
            _parameter_schema_shapes(
                root,
                target,
                seen_refs={*seen_refs, reference},
            )
            if target is not None
            else None
        )
    for union_key in ("anyOf", "oneOf"):
        choices = schema.get(union_key)
        if not isinstance(choices, list) or not choices:
            continue
        kinds: set[str] = set()
        for choice in cast(list[object], choices):
            if not isinstance(choice, Mapping):
                return None
            choice_shapes = _parameter_schema_shapes(
                root,
                cast(Mapping[str, Any], choice),
                seen_refs=set(seen_refs),
            )
            if choice_shapes is None:
                return None
            kinds.update(choice_shapes)
        return frozenset(kinds)

    schema_type = cast(object, schema.get("type"))
    raw_types: list[object] = (
        cast(list[object], schema_type)
        if isinstance(schema_type, list)
        else [schema_type]
    )
    if any(item is not None for item in raw_types):
        shapes: set[str] = set()
        for item in raw_types:
            if item == "null":
                shapes.add("null")
            elif isinstance(item, str) and item in _SCALAR_HEADER_TYPES:
                shapes.add("scalar")
            elif item == "array" and _array_items_are_non_null_scalar(
                root,
                schema,
                seen_refs=set(seen_refs),
            ):
                shapes.add("sequence")
            else:
                return None
        return frozenset(shapes)

    if "const" in schema:
        value = schema["const"]
        if value is None:
            return frozenset({"null"})
        if isinstance(value, str | int | float | bool):
            return frozenset({"scalar"})
        return None
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        shapes: set[str] = set()
        for value in cast(list[object], enum):
            if value is None:
                shapes.add("null")
            elif isinstance(value, str | int | float | bool):
                shapes.add("scalar")
            else:
                return None
        return frozenset(shapes)
    return None


def _array_items_are_non_null_scalar(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> bool:
    items = schema.get("items")
    if isinstance(items, Mapping):
        if _parameter_schema_shapes(
            root,
            cast(Mapping[str, Any], items),
            seen_refs=set(seen_refs),
        ) != frozenset({"scalar"}):
            return False
    elif items is not None and items is not False:
        return False

    prefix_items = schema.get("prefixItems")
    if prefix_items is None:
        return isinstance(items, Mapping)
    if not isinstance(prefix_items, list) or not prefix_items:
        return False
    typed_prefix_items = cast(list[object], prefix_items)
    if any(
        not isinstance(item, Mapping)
        or _parameter_schema_shapes(
            root,
            cast(Mapping[str, Any], item),
            seen_refs=set(seen_refs),
        )
        != frozenset({"scalar"})
        for item in typed_prefix_items
    ):
        return False
    if isinstance(items, Mapping) or items is False:
        return True
    max_items = schema.get("maxItems")
    return (
        isinstance(max_items, int)
        and not isinstance(max_items, bool)
        and max_items <= len(typed_prefix_items)
    )


def _query_parameter_is_sequence(  # pyright: ignore[reportUnusedFunction]
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> bool:
    """Whether a supported query field accepts repeated wire values."""
    shapes = _parameter_schema_shapes(root, schema, seen_refs=seen_refs)
    return shapes is not None and "sequence" in shapes and "scalar" not in shapes


def _local_schema_reference(
    root: Mapping[str, Any], reference: str
) -> Mapping[str, Any] | None:
    target: object = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, Mapping) or part not in target:
            return None
        target = cast(Mapping[object, object], target)[part]
    return cast(Mapping[str, Any], target) if isinstance(target, Mapping) else None


def _is_scalar_header_schema(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> bool:
    shapes = _parameter_schema_shapes(root, schema, seen_refs=seen_refs)
    return shapes is not None and bool(shapes) and shapes <= {"scalar", "null"}


def _render_response_header_value(  # pyright: ignore[reportUnusedFunction]
    name: str, value: object, *, label: str
) -> str:
    if not isinstance(value, str | int | float | bool):
        raise ValueError(f"{label} header {name!r} must serialize to a scalar value")
    rendered = str(value).lower() if isinstance(value, bool) else str(value)
    if "\r" in rendered or "\n" in rendered:
        raise ValueError(f"{label} header {name!r} must not contain CR or LF")
    if rendered[:1] in {" ", "\t"} or rendered[-1:] in {" ", "\t"}:
        raise ValueError(
            f"{label} header {name!r} must not start or end with whitespace"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in rendered):
        raise ValueError(f"{label} header {name!r} must not contain control characters")
    try:
        rendered.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} header {name!r} must be Latin-1 encodable") from exc
    return rendered
