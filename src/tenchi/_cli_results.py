"""Versioned, JSON-serializable results shared by Tenchi CLI operations.

The command-line renderer and future tool adapters consume these same immutable
values. Explicit ``as_dict()`` methods keep the wire keys deliberate instead of
letting a serializer silently turn implementation details into a public schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

type DiagnosticSeverity = Literal["error", "warning", "hint"]
type GeneratedArtifact = Literal["feature", "use-case"]
type CheckStepStatus = Literal["passed", "failed"]


class DiagnosticPayload(TypedDict):
    code: str
    severity: DiagnosticSeverity
    message: str
    path: str
    line: int | None


class DoctorPayload(TypedDict):
    schema_version: Literal[1]
    root: str
    ok: bool
    diagnostics: list[DiagnosticPayload]


class MakePayload(TypedDict):
    schema_version: Literal[1]
    root: str
    artifact: GeneratedArtifact
    name: str
    feature: str | None
    dry_run: bool
    ok: bool
    files: list[str]
    next_steps: list[str]
    error: str | None


class CheckStepPayload(TypedDict):
    name: str
    command: list[str]
    status: CheckStepStatus
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


class CheckCountsPayload(TypedDict):
    passed: int
    failed: int
    total: int


class CheckPayload(TypedDict):
    schema_version: Literal[1]
    root: str
    ok: bool
    counts: CheckCountsPayload
    duration_seconds: float
    steps: list[CheckStepPayload]
    error: str | None


class RouteResponsePayload(TypedDict):
    status: int


class RouteErrorPayload(TypedDict):
    code: str
    status: int


class RouteEntryPayload(TypedDict):
    method: str
    path: str
    status: int | None
    responses: list[RouteResponsePayload]
    use_case: str
    errors: list[RouteErrorPayload]
    tags: list[str]
    public: bool
    summary: str | None
    response_headers: str | None
    deprecated: bool | str
    sunset: str | None
    max_request_bytes: int | None
    timeout: float | None


class RoutesPayload(TypedDict):
    schema_version: Literal[1]
    root: str
    routes: list[RouteEntryPayload]


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    """One source-anchored diagnostic in a versioned command result."""

    code: str
    severity: DiagnosticSeverity
    message: str
    path: str
    line: int | None = None

    def as_dict(self) -> DiagnosticPayload:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class DoctorResult:
    """Complete result of checking one application with ``tenchi doctor``."""

    root: str
    ok: bool
    diagnostics: tuple[DiagnosticResult, ...]
    schema_version: Literal[1] = 1

    def as_dict(self) -> DoctorPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "ok": self.ok,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class MakeResult:
    """Files and follow-up work produced or planned by a generator."""

    root: str
    artifact: GeneratedArtifact
    name: str
    feature: str | None
    dry_run: bool
    ok: bool
    files: tuple[str, ...]
    next_steps: tuple[str, ...]
    error: str | None = None
    schema_version: Literal[1] = 1

    def as_dict(self) -> MakePayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "artifact": self.artifact,
            "name": self.name,
            "feature": self.feature,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "files": list(self.files),
            "next_steps": list(self.next_steps),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class CheckStepResult:
    """One command in the application validation loop."""

    name: str
    command: tuple[str, ...]
    status: CheckStepStatus
    exit_code: int
    duration_seconds: float
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool

    def as_dict(self) -> CheckStepPayload:
        return {
            "name": self.name,
            "command": list(self.command),
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Versioned aggregate returned by ``tenchi check``."""

    root: str
    ok: bool
    steps: tuple[CheckStepResult, ...]
    duration_seconds: float
    error: str | None = None
    schema_version: Literal[1] = 1

    def as_dict(self) -> CheckPayload:
        passed = sum(step.status == "passed" for step in self.steps)
        failed = len(self.steps) - passed
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "ok": self.ok,
            "counts": {
                "passed": passed,
                "failed": failed,
                "total": len(self.steps),
            },
            "duration_seconds": self.duration_seconds,
            "steps": [step.as_dict() for step in self.steps],
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RouteEntryResult:
    """One bound route in the stable route-table result."""

    method: str
    path: str
    status: int | None
    responses: tuple[int, ...]
    use_case: str
    errors: tuple[tuple[str, int], ...]
    tags: tuple[str, ...]
    public: bool
    summary: str | None
    response_headers: str | None
    deprecated: bool | str
    sunset: str | None
    max_request_bytes: int | None
    timeout: float | None

    def as_dict(self) -> RouteEntryPayload:
        return {
            "method": self.method,
            "path": self.path,
            "status": self.status,
            "responses": [{"status": status} for status in self.responses],
            "use_case": self.use_case,
            "errors": [
                {"code": code, "status": status} for code, status in self.errors
            ],
            "tags": list(self.tags),
            "public": self.public,
            "summary": self.summary,
            "response_headers": self.response_headers,
            "deprecated": self.deprecated,
            "sunset": self.sunset,
            "max_request_bytes": self.max_request_bytes,
            "timeout": self.timeout,
        }


@dataclass(frozen=True, slots=True)
class RoutesResult:
    """Versioned route table for CLI and tool adapters."""

    root: str
    routes: tuple[RouteEntryResult, ...]
    schema_version: Literal[1] = 1

    def as_dict(self) -> RoutesPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "routes": [route.as_dict() for route in self.routes],
        }
