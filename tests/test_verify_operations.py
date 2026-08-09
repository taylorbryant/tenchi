from pathlib import Path
from types import SimpleNamespace

import pytest

from tenchi import _verify_operations
from tenchi._app_map import (
    AppMapResult,
    AppMapSource,
    AppMapSummary,
    AppMapUnresolvedReference,
)
from tenchi._cli_results import CheckResult
from tenchi._evaluation_operations import EvaluationDiffResult
from tenchi._job_operations import JobDiffResult
from tenchi._openapi_operations import (
    OpenApiDiffResult,
    OperationError,
    resolve_git_commit,
)
from tenchi._tool_operations import ToolDiffResult
from tenchi._verification_policy import (
    VerificationPolicy,
    VerificationPolicyChange,
    VerificationPolicyComparison,
    default_verification_policy,
)
from tenchi.compatibility import CompatibilityReport


def _summary(*, unresolved: int = 0) -> AppMapSummary:
    return AppMapSummary(
        features=0,
        contracts=0,
        routes=0,
        jobs=0,
        tasks=0,
        tools=0,
        evaluations=0,
        use_cases=0,
        policies=0,
        ports=0,
        adapters=0,
        contexts=0,
        entrypoints=0,
        tests=0,
        diagnostics=0,
        unresolved=unresolved,
    )


def _run_with_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_map: AppMapResult,
    *,
    policy: VerificationPolicyComparison | None = None,
    final_policy: VerificationPolicyComparison | None = None,
) -> _verify_operations.VerificationResult:
    (tmp_path / "app").mkdir()
    (tmp_path / "openapi.json").write_text("{}")
    (tmp_path / "jobs.json").write_text("{}")
    (tmp_path / "tools.json").write_text("{}")
    (tmp_path / "evaluations.json").write_text("{}")
    commit = "a" * 40

    def fake_resolve_git_commit(root: Path, ref: str) -> str:
        del root, ref
        return commit

    def fake_run_check(root: Path, **kwargs: object) -> CheckResult:
        del kwargs
        return CheckResult(
            root=str(root),
            ok=True,
            steps=(),
            duration_seconds=0.01,
        )

    if policy is None:
        strict = default_verification_policy()
        policy = VerificationPolicyComparison(
            path="tenchi.toml",
            baseline=f"{commit}:tenchi.toml",
            current=strict,
            historical=strict,
            changes=(),
        )

    policy_results = iter((policy, final_policy or policy))

    def fake_verification_policy_comparison(
        root: Path,
        *,
        ref: str,
    ) -> VerificationPolicyComparison:
        del root, ref
        return next(policy_results)

    def fake_load_route_group(root: Path, target: str) -> object:
        del root, target
        return object()

    def fake_load_task_runner(root: Path, target: str) -> SimpleNamespace:
        del root, target
        return SimpleNamespace(tasks=object())

    def fake_load_evaluation_runner(root: Path, target: str) -> SimpleNamespace:
        del root, target
        return SimpleNamespace(evaluations=object())

    def fake_load_job_group(root: Path, target: str) -> object:
        del root, target
        return object()

    def fake_load_tool_group(root: Path, target: str) -> object:
        del root, target
        return object()

    def fake_map_app(*args: object, **kwargs: object) -> AppMapResult:
        del args, kwargs
        return app_map

    def fake_tool_diff_result(root: Path, **kwargs: object) -> ToolDiffResult:
        del kwargs
        return ToolDiffResult(
            root=str(root),
            baseline=f"{commit}:tools.json",
            report=CompatibilityReport(()),
        )

    def fake_job_diff_result(root: Path, **kwargs: object) -> JobDiffResult:
        del kwargs
        return JobDiffResult(
            root=str(root),
            baseline=f"{commit}:jobs.json",
            report=CompatibilityReport(()),
        )

    def fake_evaluation_diff_result(
        root: Path, **kwargs: object
    ) -> EvaluationDiffResult:
        del kwargs
        return EvaluationDiffResult(
            root=str(root),
            baseline=f"{commit}:evaluations.json",
            report=CompatibilityReport(()),
        )

    monkeypatch.setattr(
        _verify_operations,
        "job_diff_result",
        fake_job_diff_result,
    )
    monkeypatch.setattr(
        _verify_operations,
        "resolve_git_commit",
        fake_resolve_git_commit,
    )
    monkeypatch.setattr(
        _verify_operations,
        "run_check",
        fake_run_check,
    )
    monkeypatch.setattr(
        _verify_operations,
        "verification_policy_comparison",
        fake_verification_policy_comparison,
    )
    monkeypatch.setattr(
        _verify_operations,
        "load_route_group",
        fake_load_route_group,
    )
    monkeypatch.setattr(
        _verify_operations,
        "load_task_runner",
        fake_load_task_runner,
    )
    monkeypatch.setattr(
        _verify_operations,
        "load_evaluation_runner",
        fake_load_evaluation_runner,
    )
    monkeypatch.setattr(
        _verify_operations,
        "load_job_group",
        fake_load_job_group,
    )
    monkeypatch.setattr(
        _verify_operations,
        "load_tool_group",
        fake_load_tool_group,
    )
    monkeypatch.setattr(
        _verify_operations,
        "map_app",
        fake_map_app,
    )
    monkeypatch.setattr(
        _verify_operations,
        "tool_diff_result",
        fake_tool_diff_result,
    )
    monkeypatch.setattr(
        _verify_operations,
        "evaluation_diff_result",
        fake_evaluation_diff_result,
    )
    return _verify_operations.verification_result(
        tmp_path,
        base_ref="base",
        routes="app.server.routes:api_routes",
        evaluations="app.server.evaluations:runner",
        tasks="app.server.tasks:runner",
        jobs="app.server.jobs:jobs",
        tools="app.server.tools:tools",
        title="App",
        version="1.0.0",
        description=None,
        snapshot="openapi.json",
        job_snapshot="jobs.json",
        tool_snapshot="tools.json",
        evaluation_snapshot="evaluations.json",
        security_json=None,
        timeout_seconds=10,
    )


def test_verification_preserves_other_evidence_when_one_stage_cannot_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_map = AppMapResult(
        root=str(tmp_path),
        summary=_summary(),
        nodes=(),
        edges=(),
        diagnostics=(),
        unresolved=(),
    )

    def fail_openapi(*args: object, **kwargs: object) -> OpenApiDiffResult:
        del args, kwargs
        raise OperationError("baseline is unreadable")

    monkeypatch.setattr(
        _verify_operations,
        "openapi_diff_result",
        fail_openapi,
    )

    result = _run_with_map(tmp_path, monkeypatch, app_map)

    assert result.ok is False
    assert result.check is not None and result.check.ok
    assert result.architecture is not None and result.architecture.ok
    assert result.openapi is None
    assert result.tools is not None and result.tools.report.compatible
    assert result.evaluations is not None and result.evaluations.report.compatible
    assert result.policy is not None
    openapi_requirement = next(
        item for item in result.policy.requirements if item.stage == "openapi"
    )
    assert openapi_requirement.status == "not_verifiable"
    assert result.policy.ok is False
    assert result.errors == (
        _verify_operations.VerificationErrorResult(
            stage="openapi",
            message="baseline is unreadable",
        ),
    )


def test_verification_fails_closed_on_unresolved_architecture_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unresolved = AppMapUnresolvedReference(
        code="TENCHI_MAP_UNRESOLVED",
        message="could not prove the binding",
        source=AppMapSource(path="app/server/routes.py", line=10),
    )
    app_map = AppMapResult(
        root=str(tmp_path),
        summary=_summary(unresolved=1),
        nodes=(),
        edges=(),
        diagnostics=(),
        unresolved=(unresolved,),
    )

    def fake_openapi_diff_result(root: Path, **kwargs: object) -> OpenApiDiffResult:
        del kwargs
        return OpenApiDiffResult(
            root=str(root),
            baseline=f"{'a' * 40}:openapi.json",
            report=CompatibilityReport(()),
        )

    monkeypatch.setattr(
        _verify_operations,
        "openapi_diff_result",
        fake_openapi_diff_result,
    )

    result = _run_with_map(tmp_path, monkeypatch, app_map)

    assert result.ok is False
    assert result.errors == ()
    assert result.architecture is not None
    assert result.architecture.ok is False
    assert result.architecture.unresolved == (unresolved,)
    assert result.policy is not None
    architecture_requirement = next(
        item for item in result.policy.requirements if item.stage == "architecture"
    )
    assert architecture_requirement.status == "failed"


def test_verification_runs_a_requirement_that_the_current_policy_weakens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_map = AppMapResult(
        root=str(tmp_path),
        summary=_summary(),
        nodes=(),
        edges=(),
        diagnostics=(),
        unresolved=(),
    )
    historical = default_verification_policy()
    current = VerificationPolicy(
        source="repository",
        requirements=tuple(
            (stage, "disabled" if stage == "check" else "required")
            for stage, _ in historical.requirements
        ),
    )
    policy = VerificationPolicyComparison(
        path="tenchi.toml",
        baseline=f"{'a' * 40}:tenchi.toml",
        current=current,
        historical=historical,
        changes=(
            VerificationPolicyChange(
                severity="breaking",
                stage="check",
                message="required check evidence became disabled",
            ),
        ),
    )

    def fake_openapi_diff_result(root: Path, **kwargs: object) -> OpenApiDiffResult:
        del kwargs
        return OpenApiDiffResult(
            root=str(root),
            baseline=f"{'a' * 40}:openapi.json",
            report=CompatibilityReport(()),
        )

    monkeypatch.setattr(
        _verify_operations,
        "openapi_diff_result",
        fake_openapi_diff_result,
    )

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        app_map,
        policy=policy,
    )

    assert result.ok is False
    assert result.check is not None and result.check.ok
    assert result.errors == ()
    assert result.policy is not None
    assert result.policy.compatible is False
    check_requirement = result.policy.requirements[0]
    assert check_requirement.current == "disabled"
    assert check_requirement.baseline == "required"
    assert check_requirement.enforced is True
    assert check_requirement.status == "passed"


def test_verification_rejects_policy_changes_during_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_map = AppMapResult(
        root=str(tmp_path),
        summary=_summary(),
        nodes=(),
        edges=(),
        diagnostics=(),
        unresolved=(),
    )
    historical = default_verification_policy()
    initial = VerificationPolicyComparison(
        path="tenchi.toml",
        baseline=f"{'a' * 40}:tenchi.toml",
        current=historical,
        historical=historical,
        changes=(),
    )
    weakened = VerificationPolicy(
        source="repository",
        requirements=tuple(
            (stage, "disabled" if stage == "check" else "required")
            for stage, _ in historical.requirements
        ),
    )
    final = VerificationPolicyComparison(
        path="tenchi.toml",
        baseline=initial.baseline,
        current=weakened,
        historical=historical,
        changes=(
            VerificationPolicyChange(
                severity="breaking",
                stage="check",
                message="required check evidence became disabled",
            ),
        ),
    )

    def fake_openapi_diff_result(root: Path, **kwargs: object) -> OpenApiDiffResult:
        del kwargs
        return OpenApiDiffResult(
            root=str(root),
            baseline=f"{'a' * 40}:openapi.json",
            report=CompatibilityReport(()),
        )

    monkeypatch.setattr(
        _verify_operations,
        "openapi_diff_result",
        fake_openapi_diff_result,
    )

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        app_map,
        policy=initial,
        final_policy=final,
    )

    assert result.ok is False
    assert result.check is not None and result.check.ok
    assert result.policy is not None
    assert result.policy.compatible is False
    assert result.policy.source == "repository"
    assert result.errors == (
        _verify_operations.VerificationErrorResult(
            stage="policy",
            message=(
                "verification policy changed while verification was running; "
                "rerun verify against the finished tree"
            ),
        ),
    )


@pytest.mark.parametrize("ref", ["main\x00other", "main\x1bother"])
def test_git_ref_validation_rejects_control_characters_before_running_git(
    tmp_path: Path,
    ref: str,
) -> None:
    with pytest.raises(OperationError, match="control characters"):
        resolve_git_commit(tmp_path, ref)
