import subprocess
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from tenchi import _verify_operations
from tenchi._app_map import (
    AppMapEdge,
    AppMapNode,
    AppMapResult,
    AppMapSource,
    AppMapSummary,
    AppMapUnresolvedReference,
)
from tenchi._change_plans import (
    create_contract_use_case_change_plan,
    render_change_plan,
)
from tenchi._cli_results import CheckResult
from tenchi._evaluation_operations import EvaluationDiffResult
from tenchi._job_operations import JobDiffResult
from tenchi._openapi_operations import (
    OpenApiDiffResult,
    OperationError,
    read_git_snapshot,
    resolve_git_commit,
)
from tenchi._pytest_evidence import (
    TestDefinitionIdentity as _TestDefinitionIdentity,
)
from tenchi._pytest_evidence import TestExecutionEvidence as _TestExecutionEvidence
from tenchi._source_identity import SourceIdentityError, VerificationSource
from tenchi._tool_operations import ToolDiffResult
from tenchi._verification_policy import (
    VerificationPolicy,
    VerificationPolicyChange,
    VerificationPolicyComparison,
    default_verification_policy,
)
from tenchi.compatibility import CompatibilityReport

_CHANGE_PLAN_SIGNATURE = (
    "async def create_project(request: CreateProject, context: AppContext) -> Project"
)


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
    change_plan: str | None = None,
    current_contract_signature: str = _CHANGE_PLAN_SIGNATURE,
    planned_test_evidence: _TestExecutionEvidence | None = None,
    during_verification: Callable[[], None] | None = None,
    source_error: str | None = None,
) -> _verify_operations.VerificationResult:
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "openapi.json").write_text("{}")
    (tmp_path / "jobs.json").write_text("{}")
    (tmp_path / "tools.json").write_text("{}")
    (tmp_path / "evaluations.json").write_text("{}")
    commit = "a" * 40

    def fake_resolve_git_commit(root: Path, ref: str) -> str:
        del root, ref
        return commit

    def fake_source_identity(
        root: Path,
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> VerificationSource:
        if source_error is not None:
            raise SourceIdentityError(source_error)
        digest = sha256()
        for path in sorted(root.rglob("*")):
            if checkpoint is not None:
                checkpoint()
            relative = path.relative_to(root).as_posix().encode()
            digest.update(relative)
            if path.is_symlink():
                digest.update(path.readlink().as_posix().encode())
            elif path.is_file():
                digest.update(path.read_bytes())
        return VerificationSource(
            head_commit="b" * 40,
            tree_digest=f"sha256:{digest.hexdigest()}",
            dirty=True,
        )

    def fake_run_check(root: Path, **kwargs: object) -> CheckResult:
        target = kwargs.get("test_target")
        completed = kwargs.get("test_execution_completed")
        if isinstance(target, str) and callable(completed):
            completed(
                planned_test_evidence
                or _TestExecutionEvidence(
                    target=target,
                    status="passed",
                    collected=1,
                    passed=1,
                    skipped=0,
                    xfailed=0,
                    xpassed=0,
                    failed=0,
                    errored=0,
                    deselected=0,
                    ambiguous=0,
                )
            )
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

    def fake_current_contract_signature(
        root: Path,
        plan: object,
    ) -> str:
        del root, plan
        return current_contract_signature

    def fake_tool_diff_result(root: Path, **kwargs: object) -> ToolDiffResult:
        del kwargs
        if during_verification is not None:
            during_verification()
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
        "source_identity",
        fake_source_identity,
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
        "_current_contract_signature",
        fake_current_contract_signature,
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
        change_plan=change_plan,
    )


def _change_plan_app_map(tmp_path: Path) -> AppMapResult:
    contract_source = AppMapSource(
        path="app/features/projects/contracts.py",
        line=4,
        symbol="create_project_contract",
    )
    use_case_source = AppMapSource(
        path="app/features/projects/use_cases/create_project.py",
        line=4,
        symbol="create_project",
    )
    route_source = AppMapSource(
        path="app/features/projects/routes.py",
        line=6,
    )
    test_source = AppMapSource(
        path="app/features/projects/tests/test_create_project.py",
        line=1,
    )
    nodes = (
        AppMapNode(
            id="contract:projects.create_project_contract",
            kind="contract",
            name="create_project_contract",
            source=contract_source,
            status="registered",
            feature="projects",
            details=(("method", "POST"), ("path", "/projects")),
        ),
        AppMapNode(
            id="use-case:projects.create_project",
            kind="use-case",
            name="create_project",
            source=use_case_source,
            status="registered",
            feature="projects",
        ),
        AppMapNode(
            id="route:POST /projects",
            kind="route",
            name="POST /projects",
            source=route_source,
            status="registered",
            feature="projects",
        ),
        AppMapNode(
            id="test:app/features/projects/tests/test_create_project.py",
            kind="test",
            name="test_create_project.py",
            source=test_source,
            status="declared",
            feature="projects",
        ),
    )
    edges = [
        AppMapEdge(
            kind="binds",
            source="route:POST /projects",
            target="contract:projects.create_project_contract",
            evidence=route_source,
            confidence="exact",
        ),
        AppMapEdge(
            kind="binds",
            source="route:POST /projects",
            target="use-case:projects.create_project",
            evidence=route_source,
            confidence="exact",
        ),
    ]
    edges.append(
        AppMapEdge(
            kind="depends-on",
            source="test:app/features/projects/tests/test_create_project.py",
            target="use-case:projects.create_project",
            evidence=test_source,
            confidence="exact",
        )
    )
    return AppMapResult(
        root=str(tmp_path),
        summary=_summary(),
        nodes=nodes,
        edges=tuple(edges),
        diagnostics=(),
        unresolved=(),
    )


def _write_change_plan_fixture(tmp_path: Path) -> str:
    use_case_path = tmp_path / "app/features/projects/use_cases/create_project.py"
    use_case_path.parent.mkdir(parents=True, exist_ok=True)
    use_case_path.write_text("# implemented\n", encoding="utf-8")
    test_path = tmp_path / "app/features/projects/tests/test_create_project.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        """\
from ..use_cases.create_project import create_project


def test_create_project() -> None:
    assert callable(create_project)
""",
        encoding="utf-8",
    )
    plan = create_contract_use_case_change_plan(
        baseline_ref="base",
        baseline_commit="a" * 40,
        feature="projects",
        contract_target=("app.features.projects.contracts:create_project_contract"),
        contract_name="create_project_contract",
        contract_method="POST",
        contract_path="/projects",
        use_case_name="create_project",
        use_case_signature=_CHANGE_PLAN_SIGNATURE,
    )
    relative = ".tenchi/changes/create-project.json"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(render_change_plan(plan), encoding="utf-8")
    return relative


def _stub_passing_openapi(monkeypatch: pytest.MonkeyPatch) -> None:
    def passing(root: Path, **kwargs: object) -> OpenApiDiffResult:
        del kwargs
        return OpenApiDiffResult(
            root=str(root),
            baseline=f"{'a' * 40}:openapi.json",
            report=CompatibilityReport(()),
        )

    monkeypatch.setattr(_verify_operations, "openapi_diff_result", passing)


def test_verification_proves_change_plan_postconditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_change_plan_fixture(tmp_path)
    _stub_passing_openapi(monkeypatch)

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        change_plan=path,
    )

    assert result.ok is True
    assert result.change_plan is not None
    assert result.change_plan.ok is True
    assert all(check.ok for check in result.change_plan.checks)
    assert result.change_plan.path == path


def test_verification_fails_when_a_change_plan_relationship_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_change_plan_fixture(tmp_path)
    (tmp_path / "app/features/projects/tests/test_create_project.py").write_text(
        """\
from ..use_cases.create_project import create_project


def test_create_project() -> None:
    assert True


def test_unrelated() -> None:
    assert callable(create_project)
""",
        encoding="utf-8",
    )
    _stub_passing_openapi(monkeypatch)

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        change_plan=path,
    )

    assert result.ok is False
    assert result.change_plan is not None
    assert result.change_plan.ok is False
    failed = [check for check in result.change_plan.checks if not check.ok]
    assert [(check.code, check.subject) for check in failed] == [
        (
            "edge_present",
            "app/features/projects/tests/test_create_project.py::"
            "test_create_project -> "
            "use-case:projects.create_project",
        )
    ]


def test_verification_fails_when_the_planned_test_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_change_plan_fixture(tmp_path)
    _stub_passing_openapi(monkeypatch)
    target = "app/features/projects/tests/test_create_project.py::test_create_project"

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        change_plan=path,
        planned_test_evidence=_TestExecutionEvidence(
            target=target,
            status="skipped",
            collected=1,
            passed=0,
            skipped=1,
            xfailed=0,
            xpassed=0,
            failed=0,
            errored=0,
            deselected=0,
            ambiguous=0,
        ),
    )

    assert result.ok is False
    assert result.change_plan is not None
    assert result.change_plan.test_execution.status == "skipped"
    failed = [check for check in result.change_plan.checks if not check.ok]
    assert [(check.code, check.subject, check.message) for check in failed] == [
        (
            "test_executed",
            target,
            "one or more planned test invocations were skipped",
        )
    ]


def test_change_plan_runs_its_exact_test_when_general_checks_are_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_change_plan_fixture(tmp_path)
    strict = default_verification_policy()
    disabled = VerificationPolicy(
        source="repository",
        requirements=tuple((stage, "disabled") for stage, _ in strict.requirements),
    )
    policy = VerificationPolicyComparison(
        path="tenchi.toml",
        baseline=f"{'a' * 40}:tenchi.toml",
        current=disabled,
        historical=disabled,
        changes=(),
    )
    targets: list[str] = []
    identities: list[_TestDefinitionIdentity | None] = []

    def run_exact_test(
        root: Path,
        *,
        target: str,
        identity: _TestDefinitionIdentity | None,
        timeout_seconds: float,
        cancelled: Callable[[], bool] | None = None,
    ) -> _TestExecutionEvidence:
        del root, timeout_seconds, cancelled
        targets.append(target)
        identities.append(identity)
        return _TestExecutionEvidence(
            target=target,
            status="passed",
            collected=1,
            passed=1,
            skipped=0,
            xfailed=0,
            xpassed=0,
            failed=0,
            errored=0,
            deselected=0,
            ambiguous=0,
        )

    monkeypatch.setattr(_verify_operations, "run_test_execution", run_exact_test)

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        policy=policy,
        change_plan=path,
    )

    assert result.ok is True
    assert result.check is None
    assert targets == [
        "app/features/projects/tests/test_create_project.py::test_create_project"
    ]
    assert identities == [
        _TestDefinitionIdentity(
            source=(
                tmp_path / "app/features/projects/tests/test_create_project.py"
            ).resolve(),
            first_line=4,
        )
    ]


@pytest.mark.parametrize(
    "source",
    [
        """\
from ..use_cases.create_project import create_project


def test_create_project() -> None:
    create_project = lambda: None
    assert create_project() is None
""",
        """\
from ..use_cases.create_project import create_project

create_project = lambda: None


def test_create_project() -> None:
    assert create_project() is None
""",
        """\
from ..use_cases.create_project import create_project


def test_create_project() -> None:
    assert callable(create_project)


def test_create_project() -> None:
    assert True
""",
    ],
    ids=("local-shadow", "module-rebinding", "duplicate-definition"),
)
def test_verification_rejects_an_ambiguous_planned_test_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    path = _write_change_plan_fixture(tmp_path)
    (tmp_path / "app/features/projects/tests/test_create_project.py").write_text(
        source,
        encoding="utf-8",
    )
    _stub_passing_openapi(monkeypatch)

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        change_plan=path,
    )

    assert result.ok is False
    assert result.change_plan is not None
    failed = [check for check in result.change_plan.checks if not check.ok]
    assert [(check.code, check.subject) for check in failed] == [
        (
            "edge_present",
            "app/features/projects/tests/test_create_project.py::"
            "test_create_project -> use-case:projects.create_project",
        )
    ]


def test_verification_fails_when_contract_signature_drifted_from_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_change_plan_fixture(tmp_path)
    _stub_passing_openapi(monkeypatch)

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        change_plan=path,
        current_contract_signature=(
            "async def create_project(query: ProjectQuery, request: CreateProject, "
            "context: AppContext) -> Project"
        ),
    )

    assert result.ok is False
    assert result.change_plan is not None
    failed = [check for check in result.change_plan.checks if not check.ok]
    assert [(check.code, check.subject) for check in failed] == [
        (
            "node_registered",
            "contract:projects.create_project_contract",
        )
    ]


def test_verification_fails_if_the_change_plan_changes_during_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = _write_change_plan_fixture(tmp_path)
    path = tmp_path / relative
    _stub_passing_openapi(monkeypatch)

    def replace_plan() -> None:
        replacement = create_contract_use_case_change_plan(
            baseline_ref="base",
            baseline_commit="a" * 40,
            feature="projects",
            contract_target=("app.features.projects.contracts:create_project_contract"),
            contract_name="create_project_contract",
            contract_method="POST",
            contract_path="/projects/renamed",
            use_case_name="create_project",
            use_case_signature=(
                "async def create_project(request: CreateProject, "
                "context: AppContext) -> Project"
            ),
        )
        path.write_text(render_change_plan(replacement), encoding="utf-8")

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        change_plan=relative,
        during_verification=replace_plan,
    )

    assert result.ok is False
    assert any(
        error.stage == "change_plan"
        and "changed while verification was running" in error.message
        for error in result.errors
    )


def test_verification_fails_if_source_changes_during_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_openapi(monkeypatch)

    def mutate_source() -> None:
        (tmp_path / "app/generated.py").write_text("changed = True\n")

    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        during_verification=mutate_source,
    )

    assert result.ok is False
    assert result.source is not None
    assert result.source.head_commit == "b" * 40
    assert result.source.dirty is True
    assert result.source.tree_digest.startswith("sha256:")
    assert any(
        error.stage == "source"
        and error.message
        == (
            "source changed while verification was running; rerun verify against "
            "the finished tree"
        )
        for error in result.errors
    )


def test_verification_fails_before_project_code_when_source_cannot_be_identified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_map(
        tmp_path,
        monkeypatch,
        _change_plan_app_map(tmp_path),
        source_error="could not inspect the source worktree",
    )

    assert result.ok is False
    assert result.source is None
    assert result.check is None
    assert result.architecture is None
    assert result.errors == (
        _verify_operations.VerificationErrorResult(
            stage="source",
            message="could not inspect the source worktree",
        ),
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


def test_git_baselines_ignore_repository_environment_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local = tmp_path / "local"
    local.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    for root, value in ((local, "local\n"), (other, "other\n")):
        (root / "openapi.json").write_text(value, encoding="utf-8")
        subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Tenchi Tests",
                "-c",
                "user.email=tests@tenchi.invalid",
                "commit",
                "--quiet",
                "-m",
                "initial",
            ],
            cwd=root,
            check=True,
        )
    local_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=local,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    assert resolve_git_commit(local, "HEAD") == local_commit
    baseline = read_git_snapshot(local, ref="HEAD", snapshot=Path("openapi.json"))
    assert baseline.text == "local\n"
