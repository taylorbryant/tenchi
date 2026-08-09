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

type VerificationStage = Literal[
    "baseline",
    "architecture",
    "openapi",
    "jobs",
    "tools",
    "evaluations",
]


class VerificationBaselinePayload(TypedDict):
    ref: str
    commit: str | None


class VerificationArchitecturePayload(TypedDict):
    ok: bool
    summary: AppMapSummaryPayload
    diagnostics: list[DiagnosticPayload]
    unresolved: list[AppMapUnresolvedPayload]


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
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    tenchi_version: str = __version__

    @property
    def ok(self) -> bool:
        return (
            not self.errors
            and self.check is not None
            and self.check.ok
            and self.architecture is not None
            and self.architecture.ok
            and self.openapi is not None
            and self.openapi.report.compatible
            and self.jobs is not None
            and self.jobs.report.compatible
            and self.tools is not None
            and self.tools.report.compatible
            and self.evaluations is not None
            and self.evaluations.report.compatible
        )

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
    resolved_title, resolved_version, resolved_description, resolved_security = (
        openapi_defaults(
            resolved_root,
            routes=routes,
            title=title,
            version=version,
            description=description,
            security_json=security_json,
        )
    )
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
    )


def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise CheckCancelled


def _seconds_since(started: float) -> float:
    return round(perf_counter() - started, 6)
