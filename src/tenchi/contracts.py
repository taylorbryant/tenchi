"""Contract declarations for the HTTP boundary.

A contract is pure data: method, path, the types validated at the boundary,
the success status, and the application errors the route is allowed to
return. Validation itself happens in the server, which builds Pydantic
``TypeAdapter`` instances from these declarations, so any type Pydantic can
validate works as a request, params, or response type — models, dataclasses,
``list[Model]``, and so on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic

from typing_extensions import TypeVar

from .errors import ErrorDef

ResponseT = TypeVar("ResponseT", default=Any)

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Contract(Generic[ResponseT]):
    """One declared HTTP operation.

    Build these with :func:`contract` rather than instantiating directly.
    """

    method: str
    path: str
    request: type[Any] | None = None
    params: type[Any] | None = None
    query: type[Any] | None = None
    headers: type[Any] | None = None
    response: type[ResponseT] | None = None
    status: int = 200
    errors: tuple[ErrorDef, ...] = ()
    name: str = field(default="")
    request_media_type: str = "application/json"
    response_media_type: str = "application/json"
    summary: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    deprecated: bool = False
    sunset: datetime | None = None
    max_request_bytes: int | None = None

    def declares_error(self, definition: ErrorDef) -> bool:
        """Whether this contract declares the given error as expected."""
        return definition in self.errors


def contract(
    *,
    method: str,
    path: str,
    request: type[Any] | None = None,
    params: type[Any] | None = None,
    query: type[Any] | None = None,
    headers: type[Any] | None = None,
    response: type[ResponseT] | None = None,
    status: int = 200,
    errors: Sequence[ErrorDef] = (),
    name: str | None = None,
    request_media_type: str = "application/json",
    response_media_type: str = "application/json",
    summary: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    deprecated: bool = False,
    sunset: datetime | None = None,
    max_request_bytes: int | None = None,
) -> Contract[ResponseT]:
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
            case as its ``headers`` argument. HTTP names map to field names
            by lowercasing and replacing ``-`` with ``_``, so ``X-Api-Key``
            validates into a field named ``x_api_key``. Repeated headers
            keep the last value.
        response: Type the use case result is validated against before
            serialization. ``None`` means an empty response body.
        status: Success status code. Defaults to 200.
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
        deprecated: Mark the operation as deprecated. Deprecated routes
            send a ``Deprecation: true`` response header on every
            response, and OpenAPI documents the operation as deprecated.
        sunset: The instant the route is scheduled for removal (an
            aware datetime). Emitted as an RFC 8594 ``Sunset`` response
            header and as ``x-sunset`` in the OpenAPI document. Implies
            nothing by itself — pair it with ``deprecated=True`` when
            the route is already discouraged.
        max_request_bytes: Per-route ceiling on the request body size,
            overriding ``create_app(max_request_bytes=...)``. Bodies over
            the ceiling are rejected with the framework's 413 before
            validation runs. Use for upload routes that need more than
            the app-wide default.
    """
    normalized_method = method.upper()
    if normalized_method not in _METHODS:
        raise ValueError(f"contract(path={path!r}): unsupported HTTP method {method!r}")
    if not path.startswith("/"):
        raise ValueError(f"contract path must start with '/', got {path!r}")
    if not 100 <= status <= 599:
        raise ValueError(f"contract(path={path!r}): invalid status {status}")
    if not request_media_type or not response_media_type:
        raise ValueError(f"contract(path={path!r}): media types must be non-empty")
    if sunset is not None and sunset.tzinfo is None:
        raise ValueError(
            f"contract(path={path!r}): sunset must be timezone-aware so the "
            "Sunset header is unambiguous"
        )
    if max_request_bytes is not None and max_request_bytes <= 0:
        raise ValueError(
            f"contract(path={path!r}): max_request_bytes must be positive, "
            f"got {max_request_bytes}"
        )
    return Contract(
        method=normalized_method,
        path=path,
        request=request,
        params=params,
        query=query,
        headers=headers,
        response=response,
        status=status,
        errors=tuple(errors),
        name=name or f"{normalized_method} {path}",
        request_media_type=request_media_type,
        response_media_type=response_media_type,
        summary=summary,
        description=description,
        tags=tuple(tags),
        deprecated=deprecated,
        sunset=sunset,
        max_request_bytes=max_request_bytes,
    )
