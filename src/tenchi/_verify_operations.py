"""One versioned completion receipt for an agent-written Tenchi change."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal

from typing_extensions import TypedDict

from . import __version__
from ._app_map import (
    AppMapResult,
    AppMapSummaryPayload,
    AppMapUnresolvedPayload,
    AppMapUnresolvedReference,
    map_app,
)
from ._change_plans import (
    ChangePlan,
    ChangePlanError,
    ChangePlanId,
    ChangePlanKind,
    ChangePlanSchemaVersion,
    load_change_plan,
)
from ._checks import CheckCancelled, run_check, run_test_execution
from ._cli_operations import ContractLoadError, load_contract, openapi_defaults
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
from ._generation import (
    GENERATED_INCOMPLETE_PRAGMA,
    GenerationConfigurationError,
    contract_use_case_plan,
    named_contract_annotations,
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
from ._pytest_evidence import (
    TestDefinitionIdentity,
    TestExecutionEvidence,
    TestExecutionEvidencePayload,
    unavailable_test_execution_evidence,
)
from ._source_identity import (
    SourceIdentityError,
    VerificationSource,
    VerificationSourcePayload,
    source_identity,
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
    "source",
    "policy",
    "check",
    "architecture",
    "openapi",
    "jobs",
    "tools",
    "evaluations",
    "change_plan",
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


type ChangePlanCheckCode = Literal[
    "baseline_matches",
    "file_exists",
    "incomplete_marker_removed",
    "node_registered",
    "test_present",
    "edge_present",
    "test_executed",
]


class ChangePlanCheckPayload(TypedDict):
    code: ChangePlanCheckCode
    subject: str
    ok: bool
    message: str


class ChangePlanVerificationPayload(TypedDict):
    path: str
    schema_version: ChangePlanSchemaVersion
    plan_id: ChangePlanId
    kind: ChangePlanKind
    ok: bool
    test_execution: TestExecutionEvidencePayload
    checks: list[ChangePlanCheckPayload]


class VerificationPayload(TypedDict):
    schema_version: AgentProtocolVersion
    tenchi_version: str
    root: str
    ok: bool
    baseline: VerificationBaselinePayload
    source: VerificationSourcePayload | None
    duration_seconds: float
    policy: VerificationPolicyPayload | None
    check: CheckPayload | None
    architecture: VerificationArchitecturePayload | None
    openapi: OpenApiDiffPayload | None
    jobs: JobDiffPayload | None
    tools: ToolDiffPayload | None
    evaluations: EvaluationDiffPayload | None
    change_plan: ChangePlanVerificationPayload | None
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
class ChangePlanCheckResult:
    """One fixed structural postcondition from a change plan."""

    code: ChangePlanCheckCode
    subject: str
    ok: bool
    message: str

    def as_dict(self) -> ChangePlanCheckPayload:
        return {
            "code": self.code,
            "subject": self.subject,
            "ok": self.ok,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ChangePlanVerificationResult:
    """Structural intent evidence tied to one content-addressed plan."""

    path: str
    plan: ChangePlan
    test_execution: TestExecutionEvidence
    checks: tuple[ChangePlanCheckResult, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def as_dict(self) -> ChangePlanVerificationPayload:
        return {
            "path": self.path,
            "schema_version": self.plan.schema_version,
            "plan_id": self.plan.plan_id,
            "kind": self.plan.kind,
            "ok": self.ok,
            "test_execution": self.test_execution.as_dict(),
            "checks": [check.as_dict() for check in self.checks],
        }


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
    source: VerificationSource | None = None
    policy: VerificationPolicyResult | None = None
    change_plan: ChangePlanVerificationResult | None = None
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION
    tenchi_version: str = __version__

    @property
    def ok(self) -> bool:
        return (
            not self.errors
            and self.source is not None
            and self.policy is not None
            and self.policy.ok
            and (self.change_plan is None or self.change_plan.ok)
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
            "source": self.source.as_dict() if self.source is not None else None,
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
            "change_plan": (
                self.change_plan.as_dict() if self.change_plan is not None else None
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
    change_plan: str | None = None,
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
    change_plan_report: ChangePlanVerificationResult | None = None
    initial_change_plan: ChangePlan | None = None
    change_plan_path: Path | None = None
    test_execution: TestExecutionEvidence | None = None
    test_identity: TestDefinitionIdentity | None = None
    check_attempted = False
    app_map: AppMapResult | None = None
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

    try:
        initial_source = source_identity(
            resolved_root,
            checkpoint=lambda: _raise_if_cancelled(cancelled),
        )
    except SourceIdentityError as exc:
        return VerificationResult(
            root=str(resolved_root),
            baseline_ref=base_ref,
            baseline_commit=baseline_commit,
            duration_seconds=_seconds_since(started),
            check=None,
            architecture=None,
            openapi=None,
            jobs=None,
            tools=None,
            evaluations=None,
            errors=(VerificationErrorResult("source", str(exc)),),
        )

    source_error_recorded = False

    def verify_source_unchanged() -> None:
        nonlocal source_error_recorded
        try:
            current_source = source_identity(
                resolved_root,
                checkpoint=lambda: _raise_if_cancelled(cancelled),
            )
        except SourceIdentityError as exc:
            if not source_error_recorded:
                errors.append(VerificationErrorResult("source", str(exc)))
                source_error_recorded = True
        else:
            if current_source != initial_source and not source_error_recorded:
                errors.append(
                    VerificationErrorResult(
                        "source",
                        "source changed while verification was running; rerun "
                        "verify against the finished tree",
                    )
                )
                source_error_recorded = True

    if change_plan is not None:
        try:
            change_plan_path = project_path(
                resolved_root,
                change_plan,
                label="change plan",
            )
            initial_change_plan = _read_change_plan(change_plan_path)
            test_identity = _planned_test_identity(
                resolved_root,
                initial_change_plan,
            )
        except OperationError as exc:
            errors.append(VerificationErrorResult("change_plan", str(exc)))
        except ChangePlanError as exc:
            errors.append(
                VerificationErrorResult(
                    "change_plan",
                    f"change plan {change_plan!r} is invalid: {exc}",
                )
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
        check_attempted = True

        def capture_test_execution(evidence: TestExecutionEvidence) -> None:
            nonlocal test_execution
            test_execution = evidence

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
            test_target=(
                initial_change_plan.test_target
                if initial_change_plan is not None
                else None
            ),
            test_identity=test_identity,
            test_execution_completed=(
                capture_test_execution if initial_change_plan is not None else None
            ),
        )
        verify_source_unchanged()

    if initial_change_plan is not None and test_execution is None:
        if check_attempted:
            test_execution = unavailable_test_execution_evidence(
                initial_change_plan.test_target,
                "receipt_missing",
            )
        else:
            test_execution = run_test_execution(
                resolved_root,
                target=initial_change_plan.test_target,
                identity=test_identity,
                timeout_seconds=timeout_seconds,
                cancelled=cancelled,
            )
            verify_source_unchanged()

    _raise_if_cancelled(cancelled)
    if enforced("architecture") or initial_change_plan is not None:
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
            if enforced("architecture"):
                architecture = VerificationArchitectureResult(
                    summary=app_map.summary.as_dict(),
                    diagnostics=app_map.diagnostics,
                    unresolved=app_map.unresolved,
                )
        except OperationError as exc:
            if enforced("architecture"):
                errors.append(VerificationErrorResult("architecture", str(exc)))
            if initial_change_plan is not None:
                errors.append(VerificationErrorResult("change_plan", str(exc)))
        verify_source_unchanged()

    if (
        initial_change_plan is not None
        and change_plan_path is not None
        and app_map is not None
        and test_execution is not None
    ):
        change_plan_report = _verify_change_plan(
            resolved_root,
            path=change_plan_path.relative_to(resolved_root).as_posix(),
            plan=initial_change_plan,
            baseline_commit=baseline_commit,
            app_map=app_map,
            test_execution=test_execution,
            test_identity=test_identity,
        )
        verify_source_unchanged()

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
        verify_source_unchanged()

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
        verify_source_unchanged()

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
        verify_source_unchanged()

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
        verify_source_unchanged()

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

    if initial_change_plan is not None and change_plan_path is not None:
        try:
            final_change_plan = _read_change_plan(change_plan_path)
        except ChangePlanError as exc:
            errors.append(
                VerificationErrorResult(
                    "change_plan",
                    f"change plan changed or became invalid while verification "
                    f"was running: {exc}",
                )
            )
        else:
            if final_change_plan != initial_change_plan:
                errors.append(
                    VerificationErrorResult(
                        "change_plan",
                        "change plan changed while verification was running; "
                        "rerun verify against the finished tree",
                    )
                )

    verify_source_unchanged()

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
        source=initial_source,
        policy=policy,
        change_plan=change_plan_report,
    )


def _read_change_plan(path: Path) -> ChangePlan:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ChangePlanError("change plan could not be read as UTF-8") from exc
    return load_change_plan(text)


def _verify_change_plan(
    root: Path,
    *,
    path: str,
    plan: ChangePlan,
    baseline_commit: str,
    app_map: AppMapResult,
    test_execution: TestExecutionEvidence,
    test_identity: TestDefinitionIdentity | None,
) -> ChangePlanVerificationResult:
    checks: list[ChangePlanCheckResult] = []

    def record(
        code: ChangePlanCheckCode,
        subject: str,
        ok: bool,
        passed: str,
        failed: str,
    ) -> None:
        checks.append(
            ChangePlanCheckResult(
                code=code,
                subject=subject,
                ok=ok,
                message=passed if ok else failed,
            )
        )

    record(
        "baseline_matches",
        plan.baseline_ref,
        plan.baseline_commit == baseline_commit,
        "change plan and verification use the same immutable commit",
        "change plan baseline does not match the verification baseline",
    )

    for source in (plan.use_case_source, plan.test_source):
        try:
            source_path = project_path(root, source, label="generated file")
        except OperationError:
            source_path = None
        exists = source_path is not None and source_path.is_file()
        record(
            "file_exists",
            source,
            exists,
            "required generated file exists",
            "required generated file is missing",
        )
        marker_removed = False
        if exists and source_path is not None:
            try:
                marker_removed = (
                    GENERATED_INCOMPLETE_PRAGMA
                    not in source_path.read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError):
                marker_removed = False
        record(
            "incomplete_marker_removed",
            source,
            marker_removed,
            "generated incomplete marker was removed",
            "generated incomplete marker remains or the file is unreadable",
        )

    contract_symbol = plan.contract_target.partition(":")[2]
    contract_id = f"contract:{plan.feature}.{contract_symbol}"
    use_case_id = f"use-case:{plan.feature}.{plan.use_case_name}"
    route_id = f"route:{plan.contract_method} {plan.contract_path}"
    test_id = f"test:{plan.test_source}"
    nodes = {node.id: node for node in app_map.nodes}

    contract_node = nodes.get(contract_id)
    contract_details = dict(contract_node.details) if contract_node is not None else {}
    current_signature = _current_contract_signature(root, plan)
    contract_registered = (
        contract_node is not None
        and contract_node.kind == "contract"
        and contract_node.status == "registered"
        and contract_node.name == plan.contract_name
        and contract_node.source.symbol == contract_symbol
        and contract_details.get("method") == plan.contract_method
        and contract_details.get("path") == plan.contract_path
        and current_signature == plan.use_case_signature
    )
    record(
        "node_registered",
        contract_id,
        contract_registered,
        "planned contract is registered with the accepted boundary signature",
        "planned contract is not registered with the accepted boundary signature",
    )

    use_case_node = nodes.get(use_case_id)
    use_case_registered = (
        use_case_node is not None
        and use_case_node.kind == "use-case"
        and use_case_node.status == "registered"
        and use_case_node.source.path == plan.use_case_source
        and use_case_node.source.symbol == plan.use_case_name
    )
    record(
        "node_registered",
        use_case_id,
        use_case_registered,
        "planned use case is registered from the generated source",
        "planned use case is not registered from the generated source",
    )

    test_node = nodes.get(test_id)
    test_present = (
        test_node is not None
        and test_node.kind == "test"
        and test_node.source.path == plan.test_source
    )
    record(
        "test_present",
        test_id,
        test_present,
        "generated feature test is present in the application map",
        "generated feature test is missing from the application map",
    )

    edges = {
        (edge.kind, edge.source, edge.target, edge.confidence) for edge in app_map.edges
    }
    for subject, expected, passed, failed in (
        (
            f"{route_id} -> {contract_id}",
            ("binds", route_id, contract_id, "exact"),
            "registered route binds the planned contract exactly",
            "no registered route binds the planned contract exactly",
        ),
        (
            f"{route_id} -> {use_case_id}",
            ("binds", route_id, use_case_id, "exact"),
            "registered route binds the planned use case exactly",
            "no registered route binds the planned use case exactly",
        ),
    ):
        record(
            "edge_present",
            subject,
            expected in edges,
            passed,
            failed,
        )

    test_dependency_subject = f"{plan.test_target} -> {use_case_id}"
    record(
        "edge_present",
        test_dependency_subject,
        test_identity is not None,
        "planned test function references the planned use case through an "
        "unshadowed import",
        "planned test target does not resolve to one function with an unshadowed "
        "direct use-case import",
    )

    record(
        "test_executed",
        plan.test_target,
        test_execution.ok,
        "every collected invocation of the planned test passed",
        _test_execution_failure_message(test_execution),
    )

    return ChangePlanVerificationResult(
        path=path,
        plan=plan,
        test_execution=test_execution,
        checks=tuple(checks),
    )


def _planned_test_identity(
    root: Path,
    plan: ChangePlan,
) -> TestDefinitionIdentity | None:
    try:
        source_path = project_path(root, plan.test_source, label="generated test")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except (OperationError, OSError, UnicodeError, SyntaxError):
        return None

    _, separator, test_name = plan.test_target.rpartition("::")
    if not separator:
        return None
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == test_name
    ]
    if len(functions) != 1:
        return None
    function = functions[0]

    expected_module = f"app.features.{plan.feature}.use_cases.{plan.use_case_name}"
    test_module = plan.test_source.removesuffix(".py").replace("/", ".")
    imports: list[tuple[ast.ImportFrom, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_module = _absolute_import_from(test_module, node)
        if imported_module != expected_module:
            continue
        for alias in node.names:
            if alias.name == plan.use_case_name:
                imports.append((node, alias.asname or alias.name))
    if len(imports) != 1:
        return None
    import_node, local_name = imports[0]
    module_bindings = _scope_bindings(tree.body, local_name)
    if len(module_bindings) != 1 or module_bindings[0] is not import_node:
        return None

    function_reference = _FunctionReference(local_name)
    function_reference.record_arguments(function.args)
    for statement in function.body:
        function_reference.visit(statement)
    if function_reference.bound or not function_reference.loaded:
        return None

    first_line = min(
        [function.lineno, *(item.lineno for item in function.decorator_list)]
    )
    return TestDefinitionIdentity(source=source_path, first_line=first_line)


def _scope_bindings(statements: list[ast.stmt], name: str) -> list[ast.AST]:
    reference = _FunctionReference(name)
    for statement in statements:
        reference.visit(statement)
    return reference.bindings


class _FunctionReference(ast.NodeVisitor):
    """Resolve one name within a module or function scope without nested scopes."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.bindings: list[ast.AST] = []
        self.loaded = False

    @property
    def bound(self) -> bool:
        return bool(self.bindings)

    def record_arguments(self, arguments: ast.arguments) -> None:
        positional = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        for argument in positional:
            if argument.arg == self.name:
                self.bindings.append(argument)
        for argument in (arguments.vararg, arguments.kwarg):
            if argument is not None and argument.arg == self.name:
                self.bindings.append(argument)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != self.name:
            return
        if isinstance(node.ctx, ast.Load):
            self.loaded = True
        else:
            self.bindings.append(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.partition(".")[0]
            if bound_name == self.name:
                self.bindings.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if any((alias.asname or alias.name) == self.name for alias in node.names):
            self.bindings.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node)

    def visit_AsyncFunctionDef(
        self,
        node: ast.AsyncFunctionDef,
    ) -> None:
        self._visit_definition(node)

    def _visit_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name == self.name:
            self.bindings.append(node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_argument_defaults(node.args)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name == self.name:
            self.bindings.append(node)
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_argument_defaults(node.args)

    def _visit_argument_defaults(self, arguments: ast.arguments) -> None:
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name == self.name:
            self.bindings.append(node)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        if self.name in node.names:
            self.bindings.append(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        if self.name in node.names:
            self.bindings.append(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name == self.name:
            self.bindings.append(node)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name == self.name:
            self.bindings.append(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest == self.name:
            self.bindings.append(node)
        self.generic_visit(node)


def _absolute_import_from(test_module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    package = test_module.rpartition(".")[0]
    parts = package.split(".") if package else []
    parents = node.level - 1
    if parents > len(parts):
        return None
    base = parts[: len(parts) - parents]
    if node.module is not None:
        base.extend(node.module.split("."))
    return ".".join(base)


def _test_execution_failure_message(evidence: TestExecutionEvidence) -> str:
    return {
        "not_collected": "planned test was not collected by pytest",
        "deselected": "one or more planned test invocations were deselected",
        "skipped": "one or more planned test invocations were skipped",
        "xfailed": "one or more planned test invocations were expected failures",
        "xpassed": "one or more planned test invocations unexpectedly passed",
        "failed": "one or more planned test invocations failed",
        "errored": "planned test setup or teardown errored",
        "ambiguous": "planned test execution produced ambiguous lifecycle reports",
        "receipt_missing": "pytest did not produce planned-test execution evidence",
        "receipt_invalid": "pytest produced invalid planned-test execution evidence",
        "passed": "every collected invocation of the planned test passed",
    }[evidence.status]


def _current_contract_signature(root: Path, plan: ChangePlan) -> str | None:
    module_name, _, attribute = plan.contract_target.partition(":")
    try:
        declared = load_contract(root, module_name, attribute)
        generated = contract_use_case_plan(
            declared,
            feature=plan.feature,
            name=plan.use_case_name,
            named_annotations=named_contract_annotations(root, plan.feature),
        )
    except (ContractLoadError, GenerationConfigurationError):
        return None
    return generated.signature


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
