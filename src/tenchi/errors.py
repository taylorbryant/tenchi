"""Application error model for Tenchi.

Errors carry a stable code, an HTTP status, and optional structured details.
Contracts declare the errors they expect; the server maps expected errors to
HTTP responses and keeps framework-owned errors distinguishable from
application-owned ones via the ``x-tenchi-error-source`` response header.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ERROR_SOURCE_HEADER = "x-tenchi-error-source"
REQUEST_ID_HEADER = "x-request-id"


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


class AppError(Exception):
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
        super().__init__(message or definition.message)
        self.definition = definition
        self.details = details
        self.headers = dict(headers) if headers else {}

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
    """Build the standard Tenchi error response body."""
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    if request_id is not None:
        body["request_id"] = request_id
    return body


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

internal_server_error = ErrorDef(
    code="INTERNAL_SERVER_ERROR",
    status=500,
    message="Internal server error",
)
