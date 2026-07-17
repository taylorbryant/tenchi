"""Application error model for Tenchi.

Errors carry a stable code, an HTTP status, and optional structured details.
Contracts declare the errors they expect; the server maps expected errors to
HTTP responses and keeps framework-owned errors distinguishable from
application-owned ones via the ``x-tenchi-error-source`` response header.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic_core import to_jsonable_python

ERROR_SOURCE_HEADER = "x-tenchi-error-source"
REQUEST_ID_HEADER = "x-request-id"

_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_ERROR_HEADERS = {
    "connection",
    "content-length",
    "content-type",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    ERROR_SOURCE_HEADER.casefold(),
    REQUEST_ID_HEADER.casefold(),
}


class TenchiError(Exception):
    """Base class for deliberate, named Tenchi exceptions."""


class ConfigurationError(TenchiError, ValueError):
    """A declaration or composition cannot produce a valid application.

    Configuration failures are deterministic and happen before serving
    requests. ``ValueError`` remains a base for compatibility with ordinary
    Python constructor validation.
    """


@dataclass(frozen=True, slots=True)
class ErrorDef:
    """One application error definition.

    Applications declare these as module constants, typically in
    ``app/shared/errors.py``, and reference the same object from use cases
    (to raise) and contracts (to declare).
    """

    code: str
    """Stable, machine-readable code such as ``"TODO_NOT_FOUND"``."""

    status: int
    """HTTP status returned when this error crosses the HTTP boundary."""

    message: str
    """Default human-readable message."""

    headers: tuple[str, ...] = ()
    """Names of response headers this error may carry, such as
    ``("Retry-After",)``. Used for documentation; values are set per
    instance via ``AppError(..., headers={...})``."""

    def __post_init__(self) -> None:
        headers = _validate_error_definition(
            code=self.code,
            status=self.status,
            message=self.message,
            headers=self.headers,
        )
        object.__setattr__(self, "headers", headers)


class AppError(TenchiError):
    """Application error raised from use cases.

    Details become public response data when the error crosses the HTTP
    boundary, so only include values that are safe to expose. Keep provider
    errors and diagnostics in logs or ``__cause__`` instead.
    """

    definition: ErrorDef
    details: Any
    headers: dict[str, str]

    def __init__(
        self,
        definition: ErrorDef,
        *,
        message: str | None = None,
        details: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        definition, message = _validated_app_error_values(definition, message)
        validated_headers = _validated_app_error_headers(definition, headers)
        super().__init__(message or definition.message)
        self.definition = definition
        self.details = details
        self.headers = validated_headers

    @property
    def code(self) -> str:
        return self.definition.code

    @property
    def status(self) -> int:
        return self.definition.status

    @property
    def message(self) -> str:
        return str(self)


def error_body(
    *,
    code: str,
    message: str,
    details: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the standard Tenchi error response body.

    ``details`` is coerced to JSON-safe data (datetimes to ISO strings,
    unknown objects via ``str``) so a declared error always renders as its
    declared status rather than crashing serialization into a 500.
    """
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = to_jsonable_python(details, fallback=str)
    if request_id is not None:
        body["request_id"] = request_id
    return body


def _validated_error_defs(  # pyright: ignore[reportUnusedFunction]
    value: object, *, label: str
) -> tuple[ErrorDef, ...]:
    """Normalize and validate a public sequence of error declarations."""
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise ConfigurationError(f"{label} must be a sequence of ErrorDef values")
    definitions: list[ErrorDef] = []
    by_code: dict[str, ErrorDef] = {}
    for index, definition in enumerate(cast(Sequence[object], value)):
        if not isinstance(definition, ErrorDef):
            raise ConfigurationError(
                f"{label}[{index}] must be an ErrorDef, got {type(definition).__name__}"
            )
        existing = by_code.get(definition.code)
        if existing is not None:
            if existing != definition:
                raise ConfigurationError(
                    f"{label} contains conflicting ErrorDef declarations for code "
                    f"{definition.code!r}"
                )
            continue
        by_code[definition.code] = definition
        definitions.append(definition)
    return tuple(definitions)


def _validated_app_error_values(
    definition: object, message: object
) -> tuple[ErrorDef, str | None]:
    if not isinstance(definition, ErrorDef):
        raise ConfigurationError(
            f"AppError definition must be an ErrorDef, got {type(definition).__name__}"
        )
    if message is not None and not isinstance(message, str):
        raise ConfigurationError(
            f"AppError({definition.code}): message must be a string or None, "
            f"got {type(message).__name__}"
        )
    return definition, message


def _validate_error_definition(
    *, code: object, status: object, message: object, headers: object
) -> tuple[str, ...]:
    if not isinstance(code, str) or _ERROR_CODE.fullmatch(code) is None:
        raise ConfigurationError("ErrorDef code must be non-empty SCREAMING_SNAKE_CASE")
    if (
        not isinstance(status, int)
        or isinstance(status, bool)
        or not 400 <= status <= 599
    ):
        raise ConfigurationError("ErrorDef status must be between 400 and 599")
    if not isinstance(message, str) or not message.strip():
        raise ConfigurationError("ErrorDef message must be non-empty")
    if isinstance(headers, str | bytes) or not isinstance(headers, Sequence):
        raise ConfigurationError("ErrorDef headers must be a sequence of names")
    seen: set[str] = set()
    validated: list[str] = []
    for name in cast(Sequence[object], headers):
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError("ErrorDef header names must be non-empty strings")
        normalized = name.casefold()
        if _HEADER_NAME.fullmatch(name) is None:
            raise ConfigurationError(
                f"ErrorDef header {name!r} is not a valid HTTP header name"
            )
        if normalized in _RESERVED_ERROR_HEADERS:
            raise ConfigurationError(
                f"ErrorDef header {name!r} is reserved by the Tenchi framework"
            )
        if normalized in seen:
            raise ConfigurationError(
                f"ErrorDef header {name!r} is declared more than once"
            )
        seen.add(normalized)
        validated.append(name)
    return tuple(validated)


def _validated_app_error_headers(definition: ErrorDef, value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigurationError(
            f"AppError({definition.code}): headers must be a mapping"
        )
    declared = {name.casefold() for name in definition.headers}
    validated: dict[str, str] = {}
    provided: set[str] = set()
    for name, header_value in cast(Mapping[object, object], value).items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise ConfigurationError(
                f"AppError({definition.code}): header names and values must be strings"
            )
        normalized = name.casefold()
        if normalized not in declared:
            raise ConfigurationError(
                f"AppError({definition.code}): header {name!r} is not declared "
                "by the ErrorDef"
            )
        if normalized in provided:
            raise ConfigurationError(
                f"AppError({definition.code}): header {name!r} is provided more "
                "than once"
            )
        if "\r" in header_value or "\n" in header_value:
            raise ConfigurationError(
                f"AppError({definition.code}): header {name!r} value must not "
                "contain CR or LF"
            )
        if header_value[:1] in {" ", "\t"} or header_value[-1:] in {" ", "\t"}:
            raise ConfigurationError(
                f"AppError({definition.code}): header {name!r} value must not "
                "start or end with whitespace"
            )
        if any(
            ord(character) < 32 or ord(character) == 127 for character in header_value
        ):
            raise ConfigurationError(
                f"AppError({definition.code}): header {name!r} value must not "
                "contain control characters"
            )
        try:
            header_value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ConfigurationError(
                f"AppError({definition.code}): header {name!r} value must be "
                "Latin-1 encodable"
            ) from exc
        provided.add(normalized)
        validated[name] = header_value
    return validated


# Framework-owned errors. These are reserved for the Tenchi runtime; expected
# application errors should be declared per-app instead of reusing these.

validation_error = ErrorDef(
    code="VALIDATION_ERROR",
    status=422,
    message="Request validation failed",
)

not_found = ErrorDef(
    code="NOT_FOUND",
    status=404,
    message="Route not found",
)

method_not_allowed = ErrorDef(
    code="METHOD_NOT_ALLOWED",
    status=405,
    message="Method not allowed",
)

request_too_large = ErrorDef(
    code="REQUEST_TOO_LARGE",
    status=413,
    message="Request body too large",
)

unsupported_media_type = ErrorDef(
    code="UNSUPPORTED_MEDIA_TYPE",
    status=415,
    message="Request media type does not match the contract",
)

request_timeout = ErrorDef(
    code="REQUEST_TIMEOUT",
    status=504,
    message="Request deadline exceeded",
)

internal_server_error = ErrorDef(
    code="INTERNAL_SERVER_ERROR",
    status=500,
    message="Internal server error",
)
