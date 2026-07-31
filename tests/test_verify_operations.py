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
from tenchi._openapi_operations import (
    OpenApiDiffResult,
    OperationError,
    resolve_git_commit,
)
from tenchi._tool_operations import ToolDiffResult
from tenchi.compatibility import CompatibilityReport


def _summary(*, unresolved: int = 0) -> AppMapSummary:
    return AppMapSummary(
        features=0,
        contracts=0,
        routes=0,
        jobs=0,
        tasks=0,
        tools=0,
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
) -> _verify_operations.VerificationResult:
    (tmp_path / "app").mkdir()
    (tmp_path / "openapi.json").write_text("{}")
    (tmp_path / "tools.json").write_text("{}")
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

    def fake_load_route_group(root: Path, target: str) -> object:
        del root, target
        return object()

    def fake_load_task_runner(root: Path, target: str) -> SimpleNamespace:
        del root, target
        return SimpleNamespace(tasks=object())

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
    return _verify_operations.verification_result(
        tmp_path,
        base_ref="base",
        routes="app.server.routes:api_routes",
        tasks="app.server.tasks:runner",
        jobs="app.server.jobs:jobs",
        tools="app.server.tools:tools",
        title="App",
        version="1.0.0",
        description=None,
        snapshot="openapi.json",
        tool_snapshot="tools.json",
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


@pytest.mark.parametrize("ref", ["main\x00other", "main\x1bother"])
def test_git_ref_validation_rejects_control_characters_before_running_git(
    tmp_path: Path,
    ref: str,
) -> None:
    with pytest.raises(OperationError, match="control characters"):
        resolve_git_commit(tmp_path, ref)
