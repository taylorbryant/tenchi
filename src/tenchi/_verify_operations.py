"""One versioned completion receipt for an agent-written Tenchi change."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal

from typing_extensions import TypedDict

from . import __version__
from ._app_map import (
    AppMapSummaryPayload,
    AppMapUnresolvedPayload,
    AppMapUnresolvedReference,
    map_app,
)
from ._checks import CheckCancelled, run_check
from ._cli_operations import openapi_defaults
from ._cli_results import (
    AGENT_PROTOCOL_VERSION,
    AgentProtocolVersion,
    CheckPayload,
    CheckResult,
    CheckStepResult,
    DiagnosticPayload,
    DiagnosticResult,
)
from ._evaluation_operations import (
    EvaluationDiffPayload,
    EvaluationDiffResult,
    discard_evaluation_output,
    evaluation_diff_result,
    load_evaluation_runner,
)
from ._job_operations import (
    JobDiffPayload,
    JobDiffResult,
    job_diff_result,
    load_job_group,
)
from ._openapi_operations import (
    OpenApiDiffPayload,
    OpenApiDiffResult,
    OperationError,
    load_route_group,
    openapi_diff_result,
    project_path,
    resolve_git_commit,
)
from ._task_operations import load_task_runner
from ._tool_operations import (
    ToolDiffPayload,
    ToolDiffResult,
    load_tool_group,
    tool_diff_result,
)
from ._verification_policy import (
    VERIFICATION_EVIDENCE_STAGES,
    VerificationEvidenceStage,
    VerificationPolicyChange,
    VerificationPolicyChangeSeverity,
    VerificationPolicyComparison,
    VerificationPolicySource,
    VerificationRequirement,
    default_verification_policy,
    verification_policy_comparison,
)

type VerificationStage = Literal[
    "baseline",
    "policy",
    "check",
    "architecture",
    "openapi",
    "jobs",
    "tools",
    "evaluations",
]
type VerificationEvidenceStatus = Literal[
    "passed",
    "failed",
    "skipped",
    "not_configured",
    "not_verifiable",
]


class VerificationBaselinePayload(TypedDict):
    ref: str
    commit: str | None


class VerificationArchitecturePayload(TypedDict):
    ok: bool
    summary: AppMapSummaryPayload
    diagnostics: list[DiagnosticPayload]
    unresolved: list[AppMapUnresolvedPayload]


class VerificationRequirementPayload(TypedDict):
    stage: VerificationEvidenceStage
    current: VerificationRequirement
    baseline: VerificationRequirement
    enforced: bool
    status: VerificationEvidenceStatus


class VerificationPolicyChangePayload(TypedDict):
    severity: VerificationPolicyChangeSeverity
    stage: VerificationEvidenceStage | None
    message: str


class VerificationPolicyPayload(TypedDict):
    path: str
    source: VerificationPolicySource
    baseline_source: VerificationPolicySource
    baseline: str
    ok: bool
    compatible: bool
    requirements: list[VerificationRequirementPayload]
    changes: list[VerificationPolicyChangePayload]


class VerificationErrorPayload(TypedDict):
    stage: VerificationStage
    message: str


class VerificationPayload(TypedDict):
    schema_version: AgentProtocolVersion
    tenchi_version: str
    root: str
    ok: bool
    baseline: VerificationBaselinePayload
    duration_seconds: float
    policy: VerificationPolicyPayload | None
    check: CheckPayload | None
    architecture: VerificationArchitecturePayload | None
    openapi: OpenApiDiffPayload | None
    jobs: JobDiffPayload | None
    tools: ToolDiffPayload | None
    evaluations: EvaluationDiffPayload | None
    errors: list[VerificationErrorPayload]


@dataclass(frozen=True, slots=True)
class VerificationArchitectureResult:
    """Strict architecture evidence retained in a verification receipt."""

    summary: AppMapSummaryPayload
    diagnostics: tuple[DiagnosticResult, ...]
    unresolved: tuple[AppMapUnresolvedReference, ...]

    @property
    def ok(self) -> bool:
        return not self.diagnostics and not self.unresolved

    def as_dict(self) -> VerificationArchitecturePayload:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "unresolved": [item.as_dict() for item in self.unresolved],
        }


@dataclass(frozen=True, slots=True)
class VerificationRequirementResult:
    """Declared and enforced state for one verification evidence stage."""

    stage: VerificationEvidenceStage
    current: VerificationRequirement
    baseline: VerificationRequirement
    enforced: bool
    status: VerificationEvidenceStatus

    def as_dict(self) -> VerificationRequirementPayload:
        return {
            "stage": self.stage,
            "current": self.current,
            "baseline": self.baseline,
            "enforced": self.enforced,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class VerificationPolicyResult:
    """Protected repository policy plus the evidence it required."""

    path: str
    source: VerificationPolicySource
    baseline_source: VerificationPolicySource
    baseline: str
    compatible: bool
    requirements: tuple[VerificationRequirementResult, ...]
    changes: tuple[VerificationPolicyChange, ...]

    @property
    def ok(self) -> bool:
        return self.compatible and all(
            requirement.status not in {"failed", "not_verifiable"}
            for requirement in self.requirements
        )

    def as_dict(self) -> VerificationPolicyPayload:
        return {
            "path": self.path,
            "source": self.source,
            "baseline_source": self.baseline_source,
            "baseline": self.baseline,
            "ok": self.ok,
            "compatible": self.compatible,
            "requirements": [item.as_dict() for item in self.requirements],
            "changes": [
                {
                    "severity": change.severity,
                    "stage": change.stage,
                    "message": change.message,
                }
                for change in self.changes
            ],
        }


@dataclass(frozen=True, slots=True)
class VerificationErrorResult:
    """A stage that could not produce verification evidence."""

    stage: VerificationStage
    message: str

    def as_dict(self) -> VerificationErrorPayload:
        return {"stage": self.stage, "message": self.message}


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Versioned, rerunnable evidence for one source-tree state."""

    root: str
    baseline_ref: str
    baseline_commit: str | None
    duration_seconds: float
    check: CheckResult | None
    architecture: VerificationArchitectureResult | None
    openapi: OpenApiDiffResult | None
    jobs: JobDiffResult | None
    tools: ToolDiffResult | None
    evaluations: EvaluationDiffResult | None
    errors: tuple[VerificationErrorResult, ...]
    policy: VerificationPolicyResult | None = None
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    tenchi_version: str = __version__

    @property
    def ok(self) -> bool:
        return not self.errors and self.policy is not None and self.policy.ok

    def as_dict(self) -> VerificationPayload:
        return {
            "schema_version": self.schema_version,
            "tenchi_version": self.tenchi_version,
            "root": self.root,
            "ok": self.ok,
            "baseline": {
                "ref": self.baseline_ref,
                "commit": self.baseline_commit,
            },
            "duration_seconds": self.duration_seconds,
            "policy": self.policy.as_dict() if self.policy is not None else None,
            "check": self.check.as_dict() if self.check is not None else None,
            "architecture": (
                self.architecture.as_dict() if self.architecture is not None else None
            ),
            "openapi": self.openapi.as_dict() if self.openapi is not None else None,
            "jobs": self.jobs.as_dict() if self.jobs is not None else None,
            "tools": self.tools.as_dict() if self.tools is not None else None,
            "evaluations": (
                self.evaluations.as_dict() if self.evaluations is not None else None
            ),
            "errors": [error.as_dict() for error in self.errors],
        }


def verification_result(
    root: Path,
    *,
    base_ref: str,
    routes: str,
    evaluations: str,
    tasks: str,
    jobs: str,
    tools: str,
    title: str | None,
    version: str | None,
    description: str | None,
    snapshot: str,
    job_snapshot: str,
    tool_snapshot: str,
    evaluation_snapshot: str,
    security_json: str | None,
    timeout_seconds: float,
    allow_missing_job_baseline: bool = False,
    allow_missing_evaluation_baseline: bool = False,
    cancelled: Callable[[], bool] | None = None,
    step_completed: Callable[[int, int, CheckStepResult], None] | None = None,
) -> VerificationResult:
    """Run local checks and compare every snapshotted boundary with one commit."""
    started = perf_counter()
    resolved_root = root.resolve()
    errors: list[VerificationErrorResult] = []
    check: CheckResult | None = None
    architecture: VerificationArchitectureResult | None = None
    openapi: OpenApiDiffResult | None = None
    job_report: JobDiffResult | None = None
    tool_report: ToolDiffResult | None = None
    evaluation_report: EvaluationDiffResult | None = None
    policy_comparison: VerificationPolicyComparison | None = None
    policy_error: str | None = None

    try:
        snapshot_path = project_path(resolved_root, snapshot)
        job_snapshot_path = project_path(resolved_root, job_snapshot)
        tool_snapshot_path = project_path(resolved_root, tool_snapshot)
        evaluation_snapshot_path = project_path(
            resolved_root,
            evaluation_snapshot,
        )
        baseline_commit = resolve_git_commit(resolved_root, base_ref)
    except OperationError as exc:
        return VerificationResult(
            root=str(resolved_root),
            baseline_ref=base_ref,
            baseline_commit=None,
            duration_seconds=_seconds_since(started),
            check=None,
            architecture=None,
            openapi=None,
            jobs=None,
            tools=None,
            evaluations=None,
            errors=(VerificationErrorResult("baseline", str(exc)),),
        )

    _raise_if_cancelled(cancelled)
    try:
        policy_comparison = verification_policy_comparison(
            resolved_root,
            ref=baseline_commit,
        )
    except OperationError as exc:
        policy_error = str(exc)
        errors.append(VerificationErrorResult("policy", policy_error))

    initial_policy_comparison = policy_comparison

    fallback_policy = default_verification_policy()

    def enforced(stage: VerificationEvidenceStage) -> bool:
        if policy_comparison is None:
            return fallback_policy.requirement(stage) == "required"
        return policy_comparison.enforced(stage)

    resolved_title = title or resolved_root.name
    resolved_version = version or "0.1.0"
    resolved_description = description
    resolved_security = security_json
    openapi_ready = not (enforced("check") or enforced("openapi"))
    if not openapi_ready:
        try:
            (
                resolved_title,
                resolved_version,
                resolved_description,
                resolved_security,
            ) = openapi_defaults(
                resolved_root,
                routes=routes,
                title=title,
                version=version,
                description=description,
                security_json=security_json,
            )
        except OperationError as exc:
            for stage in ("check", "openapi"):
                if enforced(stage):
                    errors.append(VerificationErrorResult(stage, str(exc)))
        else:
            openapi_ready = True

    if enforced("check") and openapi_ready:
        check = run_check(
            resolved_root,
            routes=routes,
            title=resolved_title,
            version=resolved_version,
            description=resolved_description,
            snapshot=str(snapshot_path),
            evaluations=evaluations,
            evaluation_snapshot=str(evaluation_snapshot_path),
            jobs=jobs,
            job_snapshot=str(job_snapshot_path),
            tools=tools,
            tool_snapshot=str(tool_snapshot_path),
            security_json=resolved_security,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
            step_completed=step_completed,
        )

    _raise_if_cancelled(cancelled)
    if enforced("architecture"):
        try:
            route_group = load_route_group(resolved_root, routes)
            with discard_evaluation_output():
                evaluation_runner = load_evaluation_runner(resolved_root, evaluations)
            task_runner = load_task_runner(resolved_root, tasks)
            job_group = load_job_group(resolved_root, jobs)
            tool_group = load_tool_group(resolved_root, tools)
            app_map = map_app(
                resolved_root,
                route_group,
                task_runner.tasks,
                job_group,
                tool_group,
                evaluation_runner.evaluations,
            )
            architecture = VerificationArchitectureResult(
                summary=app_map.summary.as_dict(),
                diagnostics=app_map.diagnostics,
                unresolved=app_map.unresolved,
            )
        except OperationError as exc:
            errors.append(VerificationErrorResult("architecture", str(exc)))

    _raise_if_cancelled(cancelled)
    if enforced("openapi") and openapi_ready:
        try:
            openapi = openapi_diff_result(
                resolved_root,
                routes=routes,
                snapshot=snapshot_path,
                ref=baseline_commit,
                title=resolved_title,
                version=resolved_version,
                description=resolved_description,
                security_json=resolved_security,
            )
        except OperationError as exc:
            errors.append(VerificationErrorResult("openapi", str(exc)))

    _raise_if_cancelled(cancelled)
    if enforced("jobs"):
        try:
            job_report = job_diff_result(
                resolved_root,
                jobs=jobs,
                snapshot=job_snapshot_path,
                ref=baseline_commit,
                allow_missing_baseline=allow_missing_job_baseline,
            )
        except OperationError as exc:
            errors.append(VerificationErrorResult("jobs", str(exc)))

    _raise_if_cancelled(cancelled)
    if enforced("tools"):
        try:
            tool_report = tool_diff_result(
                resolved_root,
                tools=tools,
                snapshot=tool_snapshot_path,
                ref=baseline_commit,
            )
        except OperationError as exc:
            errors.append(VerificationErrorResult("tools", str(exc)))

    _raise_if_cancelled(cancelled)
    if enforced("evaluations"):
        try:
            evaluation_report = evaluation_diff_result(
                resolved_root,
                evaluations=evaluations,
                snapshot=evaluation_snapshot_path,
                ref=baseline_commit,
                allow_missing_baseline=allow_missing_evaluation_baseline,
            )
        except OperationError as exc:
            errors.append(VerificationErrorResult("evaluations", str(exc)))

    _raise_if_cancelled(cancelled)
    try:
        final_policy_comparison = verification_policy_comparison(
            resolved_root,
            ref=baseline_commit,
        )
    except OperationError as exc:
        final_policy_error = str(exc)
        if final_policy_error != policy_error:
            errors.append(VerificationErrorResult("policy", final_policy_error))
        policy_comparison = None
    else:
        if (
            initial_policy_comparison is not None
            and final_policy_comparison != initial_policy_comparison
        ):
            errors.append(
                VerificationErrorResult(
                    "policy",
                    "verification policy changed while verification was running; "
                    "rerun verify against the finished tree",
                )
            )
        policy_comparison = final_policy_comparison

    policy = _policy_result(
        policy_comparison,
        check=check,
        architecture=architecture,
        openapi=openapi,
        jobs=job_report,
        tools=tool_report,
        evaluations=evaluation_report,
    )

    return VerificationResult(
        root=str(resolved_root),
        baseline_ref=base_ref,
        baseline_commit=baseline_commit,
        duration_seconds=_seconds_since(started),
        check=check,
        architecture=architecture,
        openapi=openapi,
        jobs=job_report,
        tools=tool_report,
        evaluations=evaluation_report,
        errors=tuple(errors),
        policy=policy,
    )


def _policy_result(
    comparison: VerificationPolicyComparison | None,
    *,
    check: CheckResult | None,
    architecture: VerificationArchitectureResult | None,
    openapi: OpenApiDiffResult | None,
    jobs: JobDiffResult | None,
    tools: ToolDiffResult | None,
    evaluations: EvaluationDiffResult | None,
) -> VerificationPolicyResult | None:
    if comparison is None:
        return None
    evidence: dict[VerificationEvidenceStage, bool | None] = {
        "check": check.ok if check is not None else None,
        "architecture": architecture.ok if architecture is not None else None,
        "openapi": openapi.report.compatible if openapi is not None else None,
        "jobs": jobs.report.compatible if jobs is not None else None,
        "tools": tools.report.compatible if tools is not None else None,
        "evaluations": (
            evaluations.report.compatible if evaluations is not None else None
        ),
    }
    requirements: list[VerificationRequirementResult] = []
    for stage in VERIFICATION_EVIDENCE_STAGES:
        current = comparison.current.requirement(stage)
        historical = comparison.historical.requirement(stage)
        enforced = comparison.enforced(stage)
        outcome = evidence[stage]
        if enforced:
            status: VerificationEvidenceStatus = (
                "not_verifiable"
                if outcome is None
                else "passed"
                if outcome
                else "failed"
            )
        elif current == "disabled":
            status = "skipped"
        else:
            status = "not_configured"
        requirements.append(
            VerificationRequirementResult(
                stage=stage,
                current=current,
                baseline=historical,
                enforced=enforced,
                status=status,
            )
        )
    return VerificationPolicyResult(
        path=comparison.path,
        source=comparison.current.source,
        baseline_source=comparison.historical.source,
        baseline=comparison.baseline,
        compatible=comparison.compatible,
        requirements=tuple(requirements),
        changes=comparison.changes,
    )


def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise CheckCancelled


def _seconds_since(started: float) -> float:
    return round(perf_counter() - started, 6)
