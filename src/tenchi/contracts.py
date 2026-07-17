"""Contract declarations for the HTTP boundary.

A contract is pure data: method, path, the types validated at the boundary,
the success status, and the application errors the route is allowed to
return. Validation itself happens in the server, which builds Pydantic
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
from typing import Any, Generic, Union, cast, get_args, get_origin

from typing_extensions import TypeVar

from .errors import (
    ConfigurationError,
    ErrorDef,
    _validated_error_defs,  # pyright: ignore[reportPrivateUsage]
)
from .responses import (
    SuccessDef,
    _validated_success_defs,  # pyright: ignore[reportPrivateUsage]
)

ResponseT = TypeVar("ResponseT", default=Any)
ResponseHeadersT = TypeVar("ResponseHeadersT", default=None)

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
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
    status: int = 200
    errors: tuple[ErrorDef, ...] = ()
    name: str = field(default="")
    request_media_type: str = "application/json"
    response_media_type: str = "application/json"
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    deprecated: bool | datetime = False
    sunset: datetime | None = None
    max_request_bytes: int | None = None
    successes: tuple[SuccessDef[Any, Any], ...] = ()
    timeout: float | None = None

    def declares_error(self, definition: ErrorDef) -> bool:
        """Whether this contract declares the given error as expected."""
        return definition in self.errors


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
    status: int = 200,
    errors: Sequence[ErrorDef] = (),
    name: str | None = None,
    request_media_type: str = "application/json",
    response_media_type: str = "application/json",
    summary: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    deprecated: bool | datetime = False,
    sunset: datetime | None = None,
    max_request_bytes: int | None = None,
    successes: Sequence[SuccessDef[Any, Any]] = (),
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
        response: Successful wire body type. For a singular success, the use
            case result is validated against it before serialization. With
            ``successes=``, it is the aggregate body type returned by the
            typed client; each named outcome declares its concrete wire type.
            ``None`` means an empty response body.
        response_headers: Object-shaped type describing application-owned
            headers on a successful response. A bound route must provide a
            pure ``response_headers=`` projector that derives this value from
            the validated use-case result. Field aliases are the wire names;
            underscores otherwise become hyphens. The type must declare a
            fixed set of fields that validate and serialize under the same
            names, and each field must serialize to one scalar value. Dynamic
            extra fields and framework-owned header names are rejected.
        status: Singular success status code. Defaults to 200 and is ignored
            when ``successes=`` declares named outcomes.
        errors: Application errors this route is expected to return. Expected
            errors are mapped to their HTTP status; undeclared ``AppError``
            instances are treated as internal server errors.
        name: Optional stable name, defaulting to ``"METHOD path"``.
        request_media_type: Media type of the request body. The default,
            ``application/json``, validates the body as JSON. ``text/*``
            types validate the decoded text, and any other type (such as
            ``application/octet-stream``) validates the raw bytes — pair
            them with ``request=str`` and ``request=bytes`` respectively.
        response_media_type: Media type of the success response. Non-JSON
            types send ``str``/``bytes`` results as-is.
        summary: Short one-line summary for documentation.
        description: Longer documentation text. When omitted, OpenAPI
            generation falls back to the bound use case's docstring.
        tags: Documentation tags grouping related operations.
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
        successes: Named successful outcomes for routes with multiple success
            statuses or controlled Starlette response passthrough. Each
            outcome's response and response-header types together must exactly
            equal the aggregate ``response`` and ``response_headers`` types.
            A bound route supplies a typed ``present=`` function to select an
            outcome. When omitted, the singular ``status`` and response
            declarations above apply.
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
        deprecated=deprecated,
        sunset=sunset,
        max_request_bytes=max_request_bytes,
        timeout=timeout,
    )
    declared_errors = _validated_error_defs(
        errors, label=f"contract(path={path!r}) errors"
    )
    declared_tags = _validated_tags(tags, path=path)
    declared_successes = _validated_success_defs(
        successes, label=f"contract(path={path!r}) successes"
    )

    normalized_method = method.upper()
    if normalized_method not in _METHODS:
        raise ConfigurationError(
            f"contract(path={path!r}): unsupported HTTP method {method!r}"
        )
    if not path.startswith("/"):
        raise ConfigurationError(f"contract path must start with '/', got {path!r}")
    _validate_path_template(path)
    if not 100 <= status <= 599:
        raise ConfigurationError(f"contract(path={path!r}): invalid status {status}")
    if not request_media_type.strip() or not response_media_type.strip():
        raise ConfigurationError(
            f"contract(path={path!r}): media types must be non-empty"
        )
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
    if timeout is not None and (timeout <= 0 or not isfinite(timeout)):
        raise ConfigurationError(
            f"contract(path={path!r}): timeout must be finite and positive, "
            f"got {timeout!r}"
        )
    if declared_successes:
        _validate_success_aggregates(
            path=path,
            response=response,
            response_headers=response_headers,
            successes=declared_successes,
        )
    return Contract(
        method=normalized_method,
        path=path,
        request=request,
        params=params,
        query=query,
        headers=headers,
        response=response,
        response_headers=response_headers,
        status=status,
        errors=declared_errors,
        name=name or f"{normalized_method} {path}",
        request_media_type=request_media_type,
        response_media_type=response_media_type,
        summary=summary,
        description=description,
        tags=declared_tags,
        deprecated=deprecated,
        sunset=sunset,
        max_request_bytes=max_request_bytes,
        successes=declared_successes,
        timeout=timeout,
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


def _validate_success_aggregates(
    *,
    path: str,
    response: object,
    response_headers: object,
    successes: tuple[SuccessDef[Any, Any], ...],
) -> None:
    for definition in successes:
        if not _annotation_contains(response, definition.response):
            raise ConfigurationError(
                f"contract(path={path!r}): success {definition.name!r} response "
                f"type {_type_name(definition.response)} is not represented by "
                f"the aggregate response type {_type_name(response)}"
            )
        if not _annotation_contains(response_headers, definition.response_headers):
            raise ConfigurationError(
                f"contract(path={path!r}): success {definition.name!r} "
                f"response_headers type {_type_name(definition.response_headers)} "
                "is not represented by the aggregate response_headers type "
                f"{_type_name(response_headers)}"
            )
    for label, aggregate, declared in (
        ("response", response, tuple(item.response for item in successes)),
        (
            "response_headers",
            response_headers,
            tuple(item.response_headers for item in successes),
        ),
    ):
        unexpected = _unexpected_annotation_members(aggregate, declared)
        if unexpected:
            rendered = ", ".join(_type_name(item) for item in unexpected)
            raise ConfigurationError(
                f"contract(path={path!r}): aggregate {label} type "
                f"{_type_name(aggregate)} includes {rendered}, which no success "
                "declares"
            )


def _annotation_contains(aggregate: object, member: object) -> bool:
    aggregate_members = _annotation_members(aggregate)
    return all(item in aggregate_members for item in _annotation_members(member))


def _annotation_members(annotation: object) -> tuple[object, ...]:
    normalized = type(None) if annotation is None else annotation
    origin = get_origin(normalized)
    if origin is Union or origin is UnionType:
        return cast(tuple[object, ...], get_args(normalized))
    return (normalized,)


def _unexpected_annotation_members(
    aggregate: object, declared: tuple[object, ...]
) -> tuple[object, ...]:
    declared_members = tuple(
        member for annotation in declared for member in _annotation_members(annotation)
    )
    return tuple(
        member
        for member in _annotation_members(aggregate)
        if member not in declared_members
    )


def _type_name(annotation: object) -> str:
    if annotation is None:
        return "None"
    return getattr(annotation, "__name__", repr(annotation))


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


def _is_json_media_type(  # pyright: ignore[reportUnusedFunction]
    value: str,
) -> bool:
    essence = value.partition(";")[0].strip().casefold()
    return essence == "application/json" or essence.endswith("+json")


def _is_text_media_type(  # pyright: ignore[reportUnusedFunction]
    value: str,
) -> bool:
    return value.partition(";")[0].strip().casefold().startswith("text/")


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


def _is_scalar_header_schema(
    root: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    seen_refs: set[str],
) -> bool:
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        if reference in seen_refs:
            return False
        target: object = root
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or part not in target:
                return False
            target = cast(Mapping[object, object], target)[part]
        if not isinstance(target, Mapping):
            return False
        return _is_scalar_header_schema(
            root,
            cast(Mapping[str, Any], target),
            seen_refs={*seen_refs, reference},
        )
    for union_key in ("anyOf", "oneOf"):
        choices = schema.get(union_key)
        if isinstance(choices, list):
            typed_choices = cast(list[object], choices)
            return bool(typed_choices) and all(
                isinstance(choice, Mapping)
                and _is_scalar_header_schema(
                    root,
                    cast(Mapping[str, Any], choice),
                    seen_refs=set(seen_refs),
                )
                for choice in typed_choices
            )
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type in _SCALAR_HEADER_TYPES
    if isinstance(schema_type, list):
        typed_schema_types = cast(list[object], schema_type)
        return bool(typed_schema_types) and all(
            isinstance(item, str) and item in _SCALAR_HEADER_TYPES
            for item in typed_schema_types
        )
    if "const" in schema:
        return (
            isinstance(schema["const"], str | int | float | bool)
            or schema["const"] is None
        )
    enum = schema.get("enum")
    return isinstance(enum, list) and all(
        isinstance(item, str | int | float | bool) or item is None
        for item in cast(list[object], enum)
    )


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
