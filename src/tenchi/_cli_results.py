"""Versioned, JSON-serializable results shared by Tenchi CLI operations.

The command-line renderer and future tool adapters consume these same immutable
values. Explicit ``as_dict()`` methods keep the wire keys deliberate instead of
letting a serializer silently turn implementation details into a public schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field
from typing_extensions import TypedDict

from ._change_plans import ChangePlan, ChangePlanPayload
from .evaluations import MAX_EVALUATION_TOKENS, EvaluationBudgetStatus

type DiagnosticSeverity = Literal["error", "warning", "hint"]
type GeneratedArtifact = Literal["feature", "use-case"]
type CheckStepStatus = Literal["passed", "failed"]
type PreflightCheckStatus = Literal["passed", "failed", "timed_out"]
type EvaluationKind = Literal["deterministic", "model"]
type EvaluationCaseStatus = Literal["completed", "failed", "timed_out", "skipped"]
type EvaluationRunErrorKind = Literal["unknown_evaluation", "failed"]
type EvaluationTokenCount = Annotated[
    int,
    Field(ge=0, le=MAX_EVALUATION_TOKENS),
]
type EvaluationTokenBudget = Annotated[
    int,
    Field(ge=1, le=MAX_EVALUATION_TOKENS),
]
type TaskRunErrorKind = Literal[
    "unknown_task",
    "invalid_input",
    "application_error",
    "invalid_result",
    "failed",
]
type AgentOperationName = Literal[
    "cli",
    "make",
    "routes",
    "jobs",
    "tools",
    "map",
    "openapi",
    "doctor",
    "check",
    "verify",
    "preflight",
    "eval.list",
    "eval.snapshot",
    "eval.run",
    "task.list",
    "task.run",
]
type AgentOperationErrorCode = Literal[
    "TENCHI_CLI_INVALID_ARGUMENTS",
    "TENCHI_CLI_TARGET_LOAD_FAILED",
    "TENCHI_CLI_CONFIGURATION_INVALID",
    "TENCHI_CLI_SELECTION_NOT_FOUND",
    "TENCHI_CLI_SNAPSHOT_READ_FAILED",
    "TENCHI_CLI_OPERATION_FAILED",
]
type AgentOperationErrorDetail = str | int | bool | list[str]
type AgentOperationErrorDetails = dict[str, AgentOperationErrorDetail]
type AgentProtocolVersion = Literal[11]

AGENT_PROTOCOL_VERSION: AgentProtocolVersion = 11


class AgentOperationErrorPayload(TypedDict):
    schema_version: AgentProtocolVersion
    result: Literal["operation_error"]
    operation: AgentOperationName
    ok: Literal[False]
    code: AgentOperationErrorCode
    message: str
    details: AgentOperationErrorDetails | None


class DiagnosticPayload(TypedDict):
    code: str
    severity: DiagnosticSeverity
    message: str
    path: str
    line: int | None


@dataclass(frozen=True, slots=True)
class AgentOperationErrorResult:
    """A redacted failure before an operation-specific result can be built."""

    operation: AgentOperationName
    code: AgentOperationErrorCode
    message: str
    details: AgentOperationErrorDetails | None = None
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    result: Literal["operation_error"] = "operation_error"
    ok: Literal[False] = False

    def as_dict(self) -> AgentOperationErrorPayload:
        return {
            "schema_version": self.schema_version,
            "result": self.result,
            "operation": self.operation,
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class DoctorPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    ok: bool
    diagnostics: list[DiagnosticPayload]


class MakePayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    artifact: GeneratedArtifact
    name: str
    feature: str | None
    dry_run: bool
    ok: bool
    files: list[str]
    next_steps: list[str]
    change_plan: ChangePlanPayload | None
    change_plan_path: str | None
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
    schema_version: AgentProtocolVersion
    root: str
    ok: bool
    counts: CheckCountsPayload
    duration_seconds: float
    steps: list[CheckStepPayload]
    error: str | None


class PreflightCheckPayload(TypedDict):
    name: str
    description: str | None
    status: PreflightCheckStatus
    duration_seconds: float
    failure_code: str | None


class PreflightCountsPayload(TypedDict):
    passed: int
    failed: int
    timed_out: int
    total: int


class PreflightPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    target: str
    ok: bool
    counts: PreflightCountsPayload
    duration_seconds: float
    checks: list[PreflightCheckPayload]


class EvaluationMetricDeclarationPayload(TypedDict):
    name: str
    description: str | None
    threshold: float


class EvaluationEntryPayload(TypedDict):
    name: str
    description: str | None
    kind: EvaluationKind
    case_schema: dict[str, object]
    cases: list[str]
    metrics: list[EvaluationMetricDeclarationPayload]
    timeout_seconds: float
    max_tokens: EvaluationTokenBudget | None
    max_cost_usd: float | None


class EvaluationListPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    target: str
    evaluations: list[EvaluationEntryPayload]


class EvaluationCasePayload(TypedDict):
    name: str
    status: EvaluationCaseStatus
    duration_seconds: float
    scores: dict[str, float]
    tokens: EvaluationTokenCount | None
    cost_usd: float | None
    failure_code: str | None


class EvaluationMetricPayload(TypedDict):
    name: str
    average: float | None
    threshold: float
    passed: bool
    samples: int


class EvaluationBudgetPayload(TypedDict):
    max_tokens: EvaluationTokenBudget | None
    consumed_tokens: EvaluationTokenCount | None
    max_cost_usd: float | None
    consumed_cost_usd: float | None
    status: EvaluationBudgetStatus
    passed: bool


class EvaluationOutcomePayload(TypedDict):
    name: str
    description: str | None
    kind: EvaluationKind
    ok: bool
    duration_seconds: float
    cases: list[EvaluationCasePayload]
    metrics: list[EvaluationMetricPayload]
    budget: EvaluationBudgetPayload


class EvaluationCountsPayload(TypedDict):
    completed: int
    failed: int
    timed_out: int
    skipped: int
    total: int


class EvaluationRunErrorPayload(TypedDict):
    kind: EvaluationRunErrorKind
    code: str
    message: str


class EvaluationRunPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    target: str
    name: str | None
    ok: bool
    counts: EvaluationCountsPayload
    duration_seconds: float
    evaluations: list[EvaluationOutcomePayload]
    error: EvaluationRunErrorPayload | None


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
    schema_version: AgentProtocolVersion
    root: str
    routes: list[RouteEntryPayload]


class TaskEntryPayload(TypedDict):
    name: str
    description: str | None
    input_required: bool
    input_schema: dict[str, object] | None
    output_schema: dict[str, object]


class TaskListPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    target: str
    tasks: list[TaskEntryPayload]


class TaskRunErrorPayload(TypedDict):
    kind: TaskRunErrorKind
    code: str
    message: str
    details: object | None


class TaskRunPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    target: str
    name: str
    ok: bool
    duration_seconds: float
    output: object | None
    error: TaskRunErrorPayload | None


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
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

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
    change_plan: ChangePlan | None = None
    change_plan_path: str | None = None
    error: str | None = None
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

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
            "change_plan": (
                self.change_plan.as_dict() if self.change_plan is not None else None
            ),
            "change_plan_path": self.change_plan_path,
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
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

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
class PreflightCheckResult:
    """One redacted environment preflight outcome."""

    name: str
    description: str | None
    status: PreflightCheckStatus
    duration_seconds: float
    failure_code: str | None

    def as_dict(self) -> PreflightCheckPayload:
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Versioned result of running one application's environment preflight."""

    root: str
    target: str
    ok: bool
    duration_seconds: float
    checks: tuple[PreflightCheckResult, ...]
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> PreflightPayload:
        passed = sum(check.status == "passed" for check in self.checks)
        failed = sum(check.status == "failed" for check in self.checks)
        timed_out = sum(check.status == "timed_out" for check in self.checks)
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "target": self.target,
            "ok": self.ok,
            "counts": {
                "passed": passed,
                "failed": failed,
                "timed_out": timed_out,
                "total": len(self.checks),
            },
            "duration_seconds": self.duration_seconds,
            "checks": [check.as_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class EvaluationMetricDeclarationResult:
    """One metric exposed by evaluation discovery."""

    name: str
    description: str | None
    threshold: float

    def as_dict(self) -> EvaluationMetricDeclarationPayload:
        return {
            "name": self.name,
            "description": self.description,
            "threshold": self.threshold,
        }


@dataclass(frozen=True, slots=True)
class EvaluationEntryResult:
    """One discoverable evaluation suite without its case payloads."""

    name: str
    description: str | None
    kind: EvaluationKind
    case_schema: dict[str, object]
    cases: tuple[str, ...]
    metrics: tuple[EvaluationMetricDeclarationResult, ...]
    timeout_seconds: float
    max_tokens: EvaluationTokenBudget | None
    max_cost_usd: float | None

    def as_dict(self) -> EvaluationEntryPayload:
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "case_schema": self.case_schema,
            "cases": list(self.cases),
            "metrics": [item.as_dict() for item in self.metrics],
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
        }


@dataclass(frozen=True, slots=True)
class EvaluationListResult:
    """Versioned discovery for application-owned evaluations."""

    root: str
    target: str
    evaluations: tuple[EvaluationEntryResult, ...]
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> EvaluationListPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "target": self.target,
            "evaluations": [item.as_dict() for item in self.evaluations],
        }


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """One redacted case outcome in an evaluation receipt."""

    name: str
    status: EvaluationCaseStatus
    duration_seconds: float
    scores: tuple[tuple[str, float], ...]
    tokens: EvaluationTokenCount | None
    cost_usd: float | None
    failure_code: str | None

    def as_dict(self) -> EvaluationCasePayload:
        return {
            "name": self.name,
            "status": self.status,
            "duration_seconds": self.duration_seconds,
            "scores": dict(self.scores),
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class EvaluationMetricResult:
    """One aggregated metric in an evaluation receipt."""

    name: str
    average: float | None
    threshold: float
    passed: bool
    samples: int

    def as_dict(self) -> EvaluationMetricPayload:
        return {
            "name": self.name,
            "average": self.average,
            "threshold": self.threshold,
            "passed": self.passed,
            "samples": self.samples,
        }


@dataclass(frozen=True, slots=True)
class EvaluationBudgetResult:
    """Declared and consumed usage in an evaluation receipt."""

    max_tokens: EvaluationTokenBudget | None
    consumed_tokens: EvaluationTokenCount | None
    max_cost_usd: float | None
    consumed_cost_usd: float | None
    status: EvaluationBudgetStatus

    @property
    def passed(self) -> bool:
        """Whether every declared budget was verified within its limit."""
        return self.status == "passed"

    def as_dict(self) -> EvaluationBudgetPayload:
        return {
            "max_tokens": self.max_tokens,
            "consumed_tokens": self.consumed_tokens,
            "max_cost_usd": self.max_cost_usd,
            "consumed_cost_usd": self.consumed_cost_usd,
            "status": self.status,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class EvaluationOutcomeResult:
    """Complete result for one named evaluation."""

    name: str
    description: str | None
    kind: EvaluationKind
    ok: bool
    duration_seconds: float
    cases: tuple[EvaluationCaseResult, ...]
    metrics: tuple[EvaluationMetricResult, ...]
    budget: EvaluationBudgetResult

    def as_dict(self) -> EvaluationOutcomePayload:
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "ok": self.ok,
            "duration_seconds": self.duration_seconds,
            "cases": [item.as_dict() for item in self.cases],
            "metrics": [item.as_dict() for item in self.metrics],
            "budget": self.budget.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvaluationRunErrorResult:
    """Safe top-level failure from an evaluation run."""

    kind: EvaluationRunErrorKind
    code: str
    message: str

    def as_dict(self) -> EvaluationRunErrorPayload:
        return {
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class EvaluationRunResult:
    """Versioned, redacted receipt for an explicit evaluation run."""

    root: str
    target: str
    name: str | None
    ok: bool
    duration_seconds: float
    evaluations: tuple[EvaluationOutcomeResult, ...]
    error: EvaluationRunErrorResult | None = None
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> EvaluationRunPayload:
        cases = [item for evaluation in self.evaluations for item in evaluation.cases]
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "target": self.target,
            "name": self.name,
            "ok": self.ok,
            "counts": {
                "completed": sum(item.status == "completed" for item in cases),
                "failed": sum(item.status == "failed" for item in cases),
                "timed_out": sum(item.status == "timed_out" for item in cases),
                "skipped": sum(item.status == "skipped" for item in cases),
                "total": len(cases),
            },
            "duration_seconds": self.duration_seconds,
            "evaluations": [item.as_dict() for item in self.evaluations],
            "error": self.error.as_dict() if self.error is not None else None,
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
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> RoutesPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "routes": [route.as_dict() for route in self.routes],
        }


@dataclass(frozen=True, slots=True)
class TaskEntryResult:
    """One discoverable operational task and its input/output contracts."""

    name: str
    description: str | None
    input_required: bool
    input_schema: dict[str, object] | None
    output_schema: dict[str, object]

    def as_dict(self) -> TaskEntryPayload:
        return {
            "name": self.name,
            "description": self.description,
            "input_required": self.input_required,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


@dataclass(frozen=True, slots=True)
class TaskListResult:
    """Versioned discovery result for an application's operational tasks."""

    root: str
    target: str
    tasks: tuple[TaskEntryResult, ...]
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> TaskListPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "target": self.target,
            "tasks": [item.as_dict() for item in self.tasks],
        }


@dataclass(frozen=True, slots=True)
class TaskRunErrorResult:
    """Structured failure from invoking an operational task."""

    kind: TaskRunErrorKind
    code: str
    message: str
    details: object | None = None

    def as_dict(self) -> TaskRunErrorPayload:
        return {
            "kind": self.kind,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    """Versioned result of invoking one operational task."""

    root: str
    target: str
    name: str
    ok: bool
    duration_seconds: float
    output: object | None = None
    error: TaskRunErrorResult | None = None
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> TaskRunPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "target": self.target,
            "name": self.name,
            "ok": self.ok,
            "duration_seconds": self.duration_seconds,
            "output": self.output,
            "error": self.error.as_dict() if self.error is not None else None,
        }
