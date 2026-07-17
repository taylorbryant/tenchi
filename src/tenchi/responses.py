"""Declared success outcomes and controlled Starlette response passthrough.

Most routes need only the singular success declared directly by
:func:`tenchi.contracts.contract`. Routes with more than one successful
status, or routes that must return a streaming/file/redirect response, declare
named :class:`SuccessDef` values and select one in a pure presenter.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import UnionType
from typing import Any, Generic, cast, overload

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
class SuccessDef(Generic[BodyT, HeadersT]):
    """One named successful HTTP outcome.

    Build definitions with :func:`success`. ``passthrough=True`` permits a
    presenter to return a Starlette response while the declaration still
    supplies the status, media type, body type, and header type used for
    runtime checks, OpenAPI, and the typed client.
    """

    name: str
    status: int
    response: type[BodyT] | UnionType | None
    response_headers: type[HeadersT] | UnionType | None = None
    response_media_type: str | None = "application/json"
    description: str = "Successful response"
    passthrough: bool = False

    def __post_init__(self) -> None:
        _validate_success_definition(
            name=self.name,
            status=self.status,
            response=self.response,
            response_media_type=self.response_media_type,
            description=self.description,
            passthrough=self.passthrough,
        )


@dataclass(frozen=True, slots=True)
class PresentedResponse:
    """A presenter-selected success outcome.

    Build values with :func:`present`; direct construction is intentionally
    unsupported by the server so malformed values cannot bypass its checks.
    """

    success: SuccessDef[Any, Any]
    body: Any = _UNSET
    headers: Any = _UNSET
    response: Response | None = None


@overload
def success[BodyT](
    *,
    name: str,
    status: int,
    response: type[BodyT] | UnionType,
    response_headers: None = None,
    response_media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> SuccessDef[BodyT, None]: ...


@overload
def success[BodyT, HeadersT](
    *,
    name: str,
    status: int,
    response: type[BodyT] | UnionType,
    response_headers: type[HeadersT] | UnionType,
    response_media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> SuccessDef[BodyT, HeadersT]: ...


@overload
def success(
    *,
    name: str,
    status: int,
    response: None,
    response_headers: None = None,
    response_media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> SuccessDef[None, None]: ...


@overload
def success[HeadersT](
    *,
    name: str,
    status: int,
    response: None,
    response_headers: type[HeadersT] | UnionType,
    response_media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> SuccessDef[None, HeadersT]: ...


def success(
    *,
    name: str,
    status: int,
    response: type[Any] | UnionType | None,
    response_headers: type[Any] | UnionType | None = None,
    response_media_type: str | None = "application/json",
    description: str = "Successful response",
    passthrough: bool = False,
) -> SuccessDef[Any, Any]:
    """Declare one successful outcome for ``contract(successes=...)``."""
    return SuccessDef(
        name=name,
        status=status,
        response=response,
        response_headers=response_headers,
        response_media_type=response_media_type,
        description=description,
        passthrough=passthrough,
    )


def _validate_success_definition(
    *,
    name: object,
    status: object,
    response: object,
    response_media_type: object,
    description: object,
    passthrough: object,
) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError("success: name must be a non-empty string")
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 200 <= status <= 399
    ):
        raise ConfigurationError("success: status must be an int between 200 and 399")
    if response_media_type is not None and (
        not isinstance(response_media_type, str) or not response_media_type.strip()
    ):
        raise ConfigurationError(
            "success: response_media_type must be a non-empty string or None"
        )
    if response is not None and response_media_type is None:
        raise ConfigurationError(
            "success: response_media_type cannot be None when response is declared"
        )
    if isinstance(response_media_type, str):
        try:
            validate_media_type(response_media_type)
        except MediaTypeError as exc:
            raise ConfigurationError(
                f"success: response_media_type is invalid: {exc}"
            ) from exc
    if not isinstance(description, str) or not description.strip():
        raise ConfigurationError("success: description must be a non-empty string")
    if not isinstance(passthrough, bool):
        raise ConfigurationError("success: passthrough must be a bool")
    if status in {204, 205, 304} and response is not None:
        raise ConfigurationError(
            f"success: status {status} cannot declare a response body"
        )


@overload
def present(
    success: SuccessDef[None, None],
    *,
    body: _Unset = _UNSET,
    headers: _Unset = _UNSET,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[HeadersT](
    success: SuccessDef[None, HeadersT],
    *,
    body: _Unset = _UNSET,
    headers: HeadersT,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[BodyT](
    success: SuccessDef[BodyT, None],
    *,
    body: BodyT,
    headers: _Unset = _UNSET,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[BodyT, HeadersT](
    success: SuccessDef[BodyT, HeadersT],
    *,
    body: BodyT,
    headers: HeadersT,
    response: None = None,
) -> PresentedResponse: ...


@overload
def present[BodyT, HeadersT](
    success: SuccessDef[BodyT, HeadersT],
    *,
    body: _Unset = _UNSET,
    headers: _Unset = _UNSET,
    response: Response,
) -> PresentedResponse: ...


def present(
    success: SuccessDef[Any, Any],
    *,
    body: Any = _UNSET,
    headers: Any = _UNSET,
    response: Response | None = None,
) -> PresentedResponse:
    """Select a declared outcome from a route presenter.

    Ordinary outcomes carry ``body=`` when declared and ``headers=`` when
    declared; either channel may exist independently.
    Passthrough outcomes carry exactly one Starlette ``response=``; Tenchi
    verifies its status, content type, and declared headers before returning
    it without consuming streaming bodies or replacing background tasks.
    """
    raw_success = cast(object, success)
    if not isinstance(raw_success, SuccessDef):
        raise ConfigurationError(
            f"present: success must be a SuccessDef, got {type(success).__name__}"
        )
    if success.passthrough:
        if not isinstance(response, Response):
            raise ConfigurationError(
                "present: a passthrough success requires response= to be a "
                "Starlette Response"
            )
        if body is not _UNSET or headers is not _UNSET:
            raise ConfigurationError(
                "present: body= and headers= are not accepted for a passthrough success"
            )
    else:
        if response is not None:
            raise ConfigurationError(
                "present: response= is only accepted for a passthrough success"
            )
        if (body is _UNSET) == (success.response is not None):
            requirement = (
                "requires body=" if success.response is not None else "has no body"
            )
            raise ConfigurationError(f"present: success {success.name!r} {requirement}")
        if (headers is _UNSET) == (success.response_headers is not None):
            requirement = (
                "requires headers="
                if success.response_headers is not None
                else "declares no response headers"
            )
            raise ConfigurationError(f"present: success {success.name!r} {requirement}")
    return PresentedResponse(
        success=success,
        body=body,
        headers=headers,
        response=response,
    )


def _validated_success_defs(  # pyright: ignore[reportUnusedFunction]
    value: object, *, label: str
) -> tuple[SuccessDef[Any, Any], ...]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ConfigurationError(f"{label} must be a sequence of SuccessDef values")
    definitions: list[SuccessDef[Any, Any]] = []
    names: set[str] = set()
    statuses: set[int] = set()
    for index, definition in enumerate(cast(Sequence[object], value)):
        if not isinstance(definition, SuccessDef):
            raise ConfigurationError(
                f"{label}[{index}] must be a SuccessDef, got "
                f"{type(definition).__name__}"
            )
        typed_definition = cast(SuccessDef[Any, Any], definition)
        if typed_definition.name in names:
            raise ConfigurationError(
                f"{label} declares success name {typed_definition.name!r} more "
                "than once"
            )
        if typed_definition.status in statuses:
            raise ConfigurationError(
                f"{label} declares success status {typed_definition.status} more "
                "than once; "
                "clients select outcomes by status"
            )
        names.add(typed_definition.name)
        statuses.add(typed_definition.status)
        definitions.append(typed_definition)
    return tuple(definitions)


def _is_unset(  # pyright: ignore[reportUnusedFunction]
    value: object,
) -> bool:
    return value is _UNSET
