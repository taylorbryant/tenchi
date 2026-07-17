"""Declared response variants and controlled Starlette response passthrough.

Most routes need only the singular response declared directly by
:func:`tenchi.contracts.contract`. Routes with more than one successful
status, or routes that must return a streaming/file/redirect response, declare
:class:`ResponseDef` values and select one in a pure presenter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import UnionType
from typing import Any, Generic, Union, cast, get_origin, overload

from starlette.responses import Response
from typing_extensions import TypeVar

from ._media_types import MediaTypeError, validate_media_type
from .errors import ConfigurationError

BodyT = TypeVar("BodyT", default=Any)
HeadersT = TypeVar("HeadersT", default=None)


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class ResponseDef(Generic[BodyT, HeadersT]):
    """One successful HTTP response variant.

    Build definitions with :func:`response`. ``passthrough=True`` permits a
    presenter to return a Starlette response while the declaration still
    supplies the status, media type, body type, and header type used for
    runtime checks, OpenAPI, and the typed client.
    """

    body: type[BodyT] | UnionType | None
    status: int
    headers: type[HeadersT] | None = None
    media_type: str | None = "application/json"
    description: str = "Successful response"
    passthrough: bool = False

    def __post_init__(self) -> None:
        _validate_response_definition(
            status=self.status,
            body=self.body,
            headers=self.headers,
            media_type=self.media_type,
            description=self.description,
            passthrough=self.passthrough,
        )

    @property
    def _tenchi_response_definition(self) -> None:
        """Nominal marker for the private contract-inference protocol."""
        return None


@dataclass(frozen=True, slots=True)
class PresentedResponse:
    """A presenter-selected response variant.

    Build values with :func:`present`; direct construction is intentionally
    unsupported by the server so malformed values cannot bypass its checks.
    """

    definition: ResponseDef[Any, Any]
    body: Any = _UNSET
    headers: Any = _UNSET
    response: Response | None = None


@overload
def response[BodyT](
    body: type[BodyT],
    /,
    *body_alternatives: type[BodyT],
    status: int,
    headers: None = None,
    media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> ResponseDef[BodyT, None]: ...


@overload
def response[BodyT, HeadersT](
    body: type[BodyT],
    /,
    *body_alternatives: type[BodyT],
    status: int,
    headers: type[HeadersT],
    media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> ResponseDef[BodyT, HeadersT]: ...


@overload
def response(
    body: None,
    /,
    *,
    status: int,
    headers: None = None,
    media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> ResponseDef[None, None]: ...


@overload
def response[HeadersT](
    body: None,
    /,
    *,
    status: int,
    headers: type[HeadersT],
    media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> ResponseDef[None, HeadersT]: ...


def response(
    body: type[Any] | UnionType | None,
    /,
    *body_alternatives: type[Any],
    status: int,
    headers: type[Any] | None = None,
    media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> ResponseDef[Any, Any]:
    """Declare one variant for ``contract(responses=...)``.

    Pass top-level body union members as separate positional alternatives so
    static type checkers can preserve their aggregate type::

        response(Created, Accepted, status=200)

    Nested unions remain ordinary annotations, such as
    ``response(list[Created | Accepted], status=200)``.
    """
    normalized_body = _body_annotation(body, body_alternatives)
    return ResponseDef(
        body=normalized_body,
        status=status,
        headers=headers,
        media_type=media_type,
        description=description,
        passthrough=passthrough,
    )


def _validate_response_definition(
    *,
    status: object,
    body: object,
    headers: object,
    media_type: object,
    description: object,
    passthrough: object,
) -> None:
    label = f"response(status={status!r})"
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 200 <= status <= 399
    ):
        raise ConfigurationError("response: status must be an int between 200 and 399")
    if media_type is not None and (
        not isinstance(media_type, str) or not media_type.strip()
    ):
        raise ConfigurationError(
            f"{label}: media_type must be a non-empty string or None"
        )
    if body is not None and media_type is None:
        raise ConfigurationError(
            f"{label}: media_type cannot be None when a body is declared"
        )
    if _is_union_annotation(headers):
        raise ConfigurationError(
            f"{label}: headers must be one object-shaped schema; declare "
            "separate response definitions for alternative header shapes"
        )
    if isinstance(media_type, str):
        try:
            validate_media_type(media_type)
        except MediaTypeError as exc:
            raise ConfigurationError(f"{label}: media_type is invalid: {exc}") from exc
    if not isinstance(description, str) or not description.strip():
        raise ConfigurationError(f"{label}: description must be a non-empty string")
    if not isinstance(passthrough, bool):
        raise ConfigurationError(f"{label}: passthrough must be a bool")
    if status in {204, 205, 304} and body is not None:
        raise ConfigurationError(
            f"{label}: status {status} cannot declare a response body"
        )


def _body_annotation(
    body: type[Any] | UnionType | None,
    alternatives: tuple[type[Any], ...],
) -> type[Any] | UnionType | None:
    if body is None:
        if alternatives:
            raise ConfigurationError(
                "response: body alternatives cannot follow None; use type(None) "
                "as the nullable alternative"
            )
        return None
    members: tuple[object, ...] = (body, *alternatives)
    if any(_is_union_annotation(member) for member in members):
        raise ConfigurationError(
            "response: pass top-level union members as separate positional body "
            "alternatives, for example response(A, B, status=200)"
        )
    aggregate: object = body
    for alternative in alternatives:
        try:
            aggregate = cast(Any, aggregate) | alternative
        except TypeError as exc:
            raise ConfigurationError(
                "response: body alternatives must be valid type annotations"
            ) from exc
    return cast(type[Any] | UnionType, aggregate)


def _is_union_annotation(annotation: object) -> bool:
    return isinstance(annotation, UnionType) or get_origin(annotation) in {
        Union,
        UnionType,
    }


@overload
def present(
    definition: ResponseDef[None, None],
    body: _Unset = _UNSET,
    /,
    *,
    headers: _Unset = _UNSET,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[HeadersT](
    definition: ResponseDef[None, HeadersT],
    body: _Unset = _UNSET,
    /,
    *,
    headers: HeadersT,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[BodyT](
    definition: ResponseDef[BodyT, None],
    body: BodyT,
    /,
    *,
    headers: _Unset = _UNSET,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[BodyT, HeadersT](
    definition: ResponseDef[BodyT, HeadersT],
    body: BodyT,
    /,
    *,
    headers: HeadersT,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[BodyT, HeadersT](
    definition: ResponseDef[BodyT, HeadersT],
    body: _Unset = _UNSET,
    /,
    *,
    headers: _Unset = _UNSET,
    response: Response,
) -> PresentedResponse: ...


def present(
    definition: ResponseDef[Any, Any],
    body: Any = _UNSET,
    /,
    *,
    headers: Any = _UNSET,
    response: Response | None = None,
) -> PresentedResponse:
    """Select a declared variant from a route presenter.

    Ordinary variants carry a positional body when declared and ``headers=``
    when declared; either channel may exist independently. Passthrough
    variants carry exactly one Starlette ``response=``; Tenchi verifies its
    status, content type, and declared headers before returning it without
    consuming streaming bodies or replacing background tasks.
    """
    raw_definition = cast(object, definition)
    if not isinstance(raw_definition, ResponseDef):
        raise ConfigurationError(
            "present: definition must be a ResponseDef, got "
            f"{type(definition).__name__}"
        )
    label = f"response status {definition.status}"
    if definition.passthrough:
        if not isinstance(response, Response):
            raise ConfigurationError(
                "present: a passthrough response requires response= to be a "
                "Starlette Response"
            )
        if body is not _UNSET or headers is not _UNSET:
            raise ConfigurationError(
                "present: body and headers= are not accepted for a passthrough response"
            )
    else:
        if response is not None:
            raise ConfigurationError(
                "present: response= is only accepted for a passthrough response"
            )
        if (body is _UNSET) == (definition.body is not None):
            requirement = (
                "requires a body" if definition.body is not None else "has no body"
            )
            raise ConfigurationError(f"present: {label} {requirement}")
        if (headers is _UNSET) == (definition.headers is not None):
            requirement = (
                "requires headers="
                if definition.headers is not None
                else "declares no response headers"
            )
            raise ConfigurationError(f"present: {label} {requirement}")
    return PresentedResponse(
        definition=definition,
        body=body,
        headers=headers,
        response=response,
    )


def _validated_response_defs(  # pyright: ignore[reportUnusedFunction]
    value: object, *, label: str
) -> tuple[ResponseDef[Any, Any], ...]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ConfigurationError(f"{label} must be a sequence of ResponseDef values")
    definitions: list[ResponseDef[Any, Any]] = []
    statuses: set[int] = set()
    for index, definition in enumerate(cast(Sequence[object], value)):
        if not isinstance(definition, ResponseDef):
            raise ConfigurationError(
                f"{label}[{index}] must be a ResponseDef, got "
                f"{type(definition).__name__}"
            )
        typed_definition = cast(ResponseDef[Any, Any], definition)
        if typed_definition.status in statuses:
            raise ConfigurationError(
                f"{label} declares response status {typed_definition.status} more "
                "than once; clients select variants by status"
            )
        statuses.add(typed_definition.status)
        definitions.append(typed_definition)
    return tuple(definitions)


def _is_unset(  # pyright: ignore[reportUnusedFunction]
    value: object,
) -> bool:
    return value is _UNSET
