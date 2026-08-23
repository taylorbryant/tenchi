"""The coding-agent benchmark stays isolated, strict, and payload-safe."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from benchmarks.agent_changes import cli as benchmark_cli
from benchmarks.agent_changes import core
from benchmarks.agent_changes.cli import main
from benchmarks.agent_changes.core import (
    BenchmarkError,
    evaluate_workspace,
    list_tasks,
    load_task,
    prepare_workspace,
    run_benchmark,
    summarize_results,
)
from benchmarks.agent_changes.models import (
    BENCHMARK_PROTOCOL_VERSION,
    AgentResult,
    BenchmarkResult,
    SourceResult,
    StepResult,
    benchmark_protocol_schemas,
    parse_result,
    parse_state,
    render_payload,
)

REPOSITORY_ROOT = Path(__file__).parent.parent


def test_benchmark_protocol_version_has_an_immutable_snapshot() -> None:
    snapshot_path = (
        REPOSITORY_ROOT
        / "benchmarks/agent_changes"
        / f"protocol-v{BENCHMARK_PROTOCOL_VERSION}.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert snapshot == {
        "schema_version": BENCHMARK_PROTOCOL_VERSION,
        "schemas": benchmark_protocol_schemas(),
    }


def test_repository_tasks_are_versioned_and_hidden_tests_are_valid_python() -> None:
    tasks = list_tasks()

    assert [task.id for task in tasks] == [
        "complete_todo",
        "get_todo",
        "list_todos_tool",
    ]
    for task in tasks:
        assert task.digest.startswith("sha256:")
        assert len(task.digest) == 71
        assert task.prompt.startswith("# ")
        assert task.hidden_tests
        for path in task.hidden_tests:
            ast.parse(path.read_text(encoding="utf-8"))


def test_task_digest_changes_with_hidden_acceptance_criteria(tmp_path: Path) -> None:
    source = REPOSITORY_ROOT / "benchmarks/agent_changes/tasks/get_todo"
    task_root = tmp_path / "get_todo"
    shutil.copytree(source, task_root)
    original = load_task("get_todo", tasks_root=tmp_path)
    hidden = task_root / "hidden/test_get_todo.py.tmpl"
    hidden.write_text(
        f"{hidden.read_text(encoding='utf-8')}\n# changed criterion\n",
        encoding="utf-8",
    )

    changed = load_task("get_todo", tasks_root=tmp_path)

    assert changed.digest != original.digest


def test_task_digest_changes_with_the_evaluator_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = tmp_path / "evaluator.py"
    evaluator.write_text("VERSION = 1\n", encoding="utf-8")
    monkeypatch.setattr(core, "_REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(core, "_benchmark_input_paths", lambda: (evaluator,))
    task_root = REPOSITORY_ROOT / "benchmarks/agent_changes/tasks"

    original = load_task("get_todo", tasks_root=task_root)
    evaluator.write_text("VERSION = 2\n", encoding="utf-8")
    changed = load_task("get_todo", tasks_root=task_root)

    assert changed.digest != original.digest


def test_task_loader_rejects_unknown_fields_and_path_traversal(tmp_path: Path) -> None:
    task_root = tmp_path / "bad"
    task_root.mkdir()
    (task_root / "prompt.md").write_text("# Task\n", encoding="utf-8")
    (task_root / "test.py.tmpl").write_text("def test_ok(): ...\n", encoding="utf-8")
    (task_root / "task.toml").write_text(
        """\
schema_version = 1
id = "bad"
title = "Bad"
description = "Bad task"
prompt = "../outside.md"
hidden_tests = ["test.py.tmpl"]
agent_timeout_seconds = 10
evaluation_timeout_seconds = 10
unknown = true
""",
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkError, match="missing or unsupported"):
        load_task("bad", tasks_root=tmp_path)

    source = (task_root / "task.toml").read_text(encoding="utf-8")
    (task_root / "task.toml").write_text(
        source.replace("unknown = true\n", ""), encoding="utf-8"
    )
    with pytest.raises(BenchmarkError, match="stay inside"):
        load_task("bad", tasks_root=tmp_path)

    definition = (task_root / "task.toml").read_text(encoding="utf-8")
    (task_root / "task.toml").write_text(
        definition.replace("schema_version = 1", "schema_version = true"),
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkError, match="unsupported version"):
        load_task("bad", tasks_root=tmp_path)


def test_prepare_workspace_creates_a_clean_generated_git_baseline(
    tmp_path: Path,
) -> None:
    task = load_task("complete_todo")
    workspace = tmp_path / "workspace"

    state = prepare_workspace(
        task,
        workspace,
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )

    assert state.task == task.id
    assert state.task_digest == task.digest
    assert state.workspace == str(workspace)
    assert len(state.baseline_commit) in {40, 64}
    assert (workspace / "TASK.md").read_text(encoding="utf-8") == task.prompt
    pyproject = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    assert f"tenchi @ {REPOSITORY_ROOT.as_uri()}" in pyproject
    assert (
        subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    assert parse_state(render_payload(state)) == state


def test_sync_runs_after_workspace_reaches_its_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        f"""#!{sys.executable}
from pathlib import Path

environment = Path.cwd() / ".venv/bin"
environment.mkdir(parents=True)
(environment / "tenchi").write_text(
    f"#!{{Path.cwd()}}/.venv/bin/python\\n",
    encoding="utf-8",
)
(Path.cwd() / "uv.lock").write_text("lock", encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    task = load_task("get_todo")
    workspace = tmp_path / "workspace"

    prepare_workspace(
        task,
        workspace,
        tenchi_root=REPOSITORY_ROOT,
        sync=True,
    )

    console_script = (workspace / ".venv/bin/tenchi").read_text(encoding="utf-8")
    assert console_script == f"#!{workspace}/.venv/bin/python\n"


def test_prepare_workspace_refuses_an_existing_destination(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(BenchmarkError, match="already exists"):
        prepare_workspace(
            load_task("get_todo"),
            workspace,
            tenchi_root=REPOSITORY_ROOT,
            sync=False,
        )


def test_prepare_workspace_refuses_a_different_framework_checkout(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    with pytest.raises(BenchmarkError, match="checkout containing this harness"):
        prepare_workspace(
            load_task("get_todo"),
            workspace,
            tenchi_root=tmp_path / "other-tenchi",
            sync=False,
        )

    assert not workspace.exists()


def test_prepare_workspace_ignores_repository_selection_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    redirected = tmp_path / "redirected.git"
    monkeypatch.setenv("GIT_DIR", str(redirected))

    state = prepare_workspace(
        load_task("get_todo"),
        workspace,
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )

    assert (workspace / ".git").is_dir()
    assert not redirected.exists()
    assert len(state.baseline_commit) in {40, 64}


def test_invalid_run_metadata_is_rejected_before_creating_a_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"

    with pytest.raises(BenchmarkError, match="agent label"):
        run_benchmark(
            load_task("get_todo"),
            workspace,
            tenchi_root=REPOSITORY_ROOT,
            command=(sys.executable, "-c", "pass"),
            label=" ",
            interface="cli",
            attempt=1,
            interventions=0,
            sync=False,
        )

    assert not workspace.exists()


def test_run_records_agent_changes_without_application_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    workspace = tmp_path / "workspace"
    command = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; "
        "Path('tests/test_agent_change.py').write_text("
        "'def test_agent_change(): assert True\\n', encoding='utf-8'); "
        "Path('agent-change.txt').write_text(sys.stdin.read(), encoding='utf-8')",
    )

    result = run_benchmark(
        load_task("get_todo"),
        workspace,
        tenchi_root=REPOSITORY_ROOT,
        command=command,
        label="test-agent",
        interface="cli",
        attempt=1,
        interventions=0,
        sync=False,
    )

    assert result.ok is True
    assert result.first_pass is True
    assert result.agent.status == "passed"
    assert result.task_digest == load_task("get_todo").digest
    assert result.source.changed_files == (
        "agent-change.txt",
        "tests/test_agent_change.py",
    )
    assert "Fetch one todo" in (workspace / "agent-change.txt").read_text()
    assert parse_result(render_payload(result)) == result


def test_agent_timeout_fails_the_run_and_stops_the_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)

    result = run_benchmark(
        load_task("list_todos_tool"),
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        command=(sys.executable, "-c", "import time; time.sleep(60)"),
        label="slow-agent",
        interface="unknown",
        attempt=1,
        interventions=0,
        sync=False,
        timeout_seconds=0.01,
    )

    assert result.ok is False
    assert result.first_pass is False
    assert result.agent.status == "timed_out"
    assert result.agent.exit_code is not None


def test_captured_process_output_is_bounded_while_the_process_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core, "_MAX_CAPTURE_BYTES", 64)

    run_process: Any = cast(Any, core)._run_process
    result = run_process(
        (
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'x' * 100_000)",
        ),
        cwd=tmp_path,
        timeout_seconds=5,
        capture_stdout=True,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group behavior")
def test_successful_process_cannot_leave_a_background_child(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "late-write"
    child = (
        "import time; from pathlib import Path; time.sleep(0.3); "
        f"Path({str(marker)!r}).write_text('late', encoding='utf-8')"
    )
    parent = (
        f"import subprocess, sys; subprocess.Popen((sys.executable, '-c', {child!r}))"
    )
    run_process: Any = cast(Any, core)._run_process

    result = run_process(
        (sys.executable, "-c", parent),
        cwd=tmp_path,
        timeout_seconds=5,
        capture_stdout=False,
    )
    time.sleep(0.5)

    assert result.exit_code == 0
    assert not marker.exists()


def test_evaluation_fails_when_agent_changes_the_task_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    task = load_task("complete_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    (Path(state.workspace) / "TASK.md").write_text("changed", encoding="utf-8")

    result = evaluate_workspace(task, state)

    assert result.ok is False
    assert result.steps[0].status == "failed"
    assert result.steps[0].code == "BENCHMARK_PROTECTED_INPUT_CHANGED"
    assert result.steps[1].code == "BENCHMARK_AGENT_TEST_MISSING"


def test_evaluation_rejects_changes_to_protected_tooling_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    workspace = Path(state.workspace)
    (workspace / "tests/test_agent_change.py").write_text(
        "def test_agent_change(): assert True\n",
        encoding="utf-8",
    )
    pyproject = workspace / "pyproject.toml"
    pyproject.write_text(
        f"{pyproject.read_text(encoding='utf-8')}\n# weakened by agent\n",
        encoding="utf-8",
    )

    result = evaluate_workspace(task, state)

    assert result.ok is False
    assert result.steps[0].code == "BENCHMARK_PROTECTED_INPUT_CHANGED"


def test_evaluation_rejects_a_changed_task_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )

    with pytest.raises(BenchmarkError, match="changed after"):
        evaluate_workspace(
            task,
            replace(state, task_digest=f"sha256:{'0' * 64}"),
        )


def test_evaluation_rejects_a_baseline_outside_the_workspace_history(
    tmp_path: Path,
) -> None:
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )

    with pytest.raises(BenchmarkError, match="not an ancestor"):
        evaluate_workspace(
            task,
            replace(state, baseline_commit="0" * 40),
        )


def test_repeated_evaluation_does_not_report_injected_hidden_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    task = load_task("complete_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    changed_test = Path(state.workspace) / "tests/test_agent_change.py"
    changed_test.write_text("def test_agent_change(): assert True\n", encoding="utf-8")

    first = evaluate_workspace(task, state)
    second = evaluate_workspace(task, state)

    assert first.source == second.source
    assert second.source.changed_files == ("tests/test_agent_change.py",)
    assert second.steps[1].status == "passed"


def test_agent_test_detection_uses_changes_beyond_the_reported_path_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    workspace = Path(state.workspace)
    generated = workspace / "app/generated"
    generated.mkdir()
    for index in range(201):
        (generated / f"generated_{index:03}.py").write_text("", encoding="utf-8")
    changed_test = workspace / "tests/test_agent_change.py"
    changed_test.write_text("def test_agent_change(): assert True\n", encoding="utf-8")

    result = evaluate_workspace(task, state)

    assert result.source.changed_file_count == 202
    assert len(result.source.changed_files) == 200
    assert "tests/test_agent_change.py" not in result.source.changed_files
    assert result.steps[1].status == "passed"


def test_external_agent_metadata_is_validated_before_hidden_tests_are_installed(
    tmp_path: Path,
) -> None:
    task = load_task("list_todos_tool")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    inconsistent = AgentResult(
        label="external",
        interface="cli",
        attempt=1,
        interventions=0,
        status="passed",
        exit_code=1,
        duration_seconds=1.0,
    )

    with pytest.raises(ValueError, match="exit code zero"):
        evaluate_workspace(task, state, agent=inconsistent)

    assert (
        list((Path(state.workspace) / "tests").glob("tenchi_benchmark_hidden*")) == []
    )


def test_hidden_tests_never_follow_an_agent_created_tests_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_passing_evaluation(monkeypatch)
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    workspace = Path(state.workspace)
    tests_root = workspace / "tests"
    tests_root.rename(workspace / "original-tests")
    outside = tmp_path / "outside"
    outside.mkdir()
    tests_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(BenchmarkError, match="missing or unsafe"):
        evaluate_workspace(task, state)

    assert list(outside.iterdir()) == []


def test_verify_step_uses_the_authoritative_agent_result_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    changed_test = Path(state.workspace) / "tests/test_agent_change.py"
    changed_test.write_text("def test_agent_change(): assert True\n", encoding="utf-8")

    def fake_process(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return SimpleNamespace(
            exit_code=0,
            duration_seconds=0.1,
            timed_out=False,
            stdout=b'{"ok": true}',
        )

    def passing_hidden(*args: Any, **kwargs: Any) -> StepResult:
        del args, kwargs
        return StepResult("hidden_acceptance", "passed", 0, 0.01)

    def passing_agent_tests(*args: Any, **kwargs: Any) -> StepResult:
        del args, kwargs
        return StepResult("agent_tests", "passed", 0, 0.01)

    def fake_validator(name: str, value: dict[str, object]) -> dict[str, object]:
        calls.append((name, value))
        return value

    monkeypatch.setattr(core, "_run_process", fake_process)
    monkeypatch.setattr(core, "_evaluate_agent_tests", passing_agent_tests)
    monkeypatch.setattr(core, "_evaluate_command", passing_hidden)
    monkeypatch.setattr(core, "validate_agent_result", fake_validator)

    result = evaluate_workspace(task, state)

    assert result.steps[3].status == "passed"
    assert calls == [("verify", {"ok": True})]


def test_agent_writable_virtualenv_cannot_replace_the_evaluator(
    tmp_path: Path,
) -> None:
    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        tmp_path / "workspace",
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    workspace = Path(state.workspace)
    changed_test = workspace / "tests/test_agent_change.py"
    changed_test.write_text("def test_agent_change(): assert True\n", encoding="utf-8")
    marker = workspace / "agent-evaluator-ran"
    fake_bin = workspace / ".venv/bin"
    fake_bin.mkdir(parents=True)
    for executable in ("python", "pytest"):
        fake = fake_bin / executable
        fake.write_text(
            f"#!/bin/sh\ntouch '{marker}'\nexit 0\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    evaluate_agent_tests: Any = cast(Any, core)._evaluate_agent_tests
    result = evaluate_agent_tests(
        ("tests/test_agent_change.py",),
        cwd=workspace,
        timeout_seconds=30,
    )

    assert result.status == "passed"
    assert not marker.exists()


def test_summary_reports_pass_and_first_pass_rates() -> None:
    passed = _result("get_todo", ok=True, first_pass=True)
    recovered = _result("get_todo", ok=True, first_pass=False)
    failed = _result("complete_todo", ok=False, first_pass=False)

    summary = summarize_results((passed, recovered, failed))

    assert summary.runs == 3
    assert summary.passed == 2
    assert summary.first_passed == 1
    assert summary.as_dict()["pass_rate"] == pytest.approx(2 / 3)
    assert [task.task for task in summary.tasks] == ["complete_todo", "get_todo"]


def test_render_and_summary_reject_inconsistent_result_claims() -> None:
    inconsistent = replace(_result("get_todo", ok=True, first_pass=True), ok=False)

    with pytest.raises(ValueError, match="inconsistent outcome"):
        render_payload(inconsistent)
    with pytest.raises(ValueError, match="inconsistent outcome"):
        summarize_results((inconsistent,))


def test_cli_lists_tasks_as_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", "--json"]) == 0

    output = capsys.readouterr().out
    assert '"schema_version": 1' in output
    assert '"complete_todo"' in output


def test_run_cli_accepts_options_after_the_workspace_and_splits_agent_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: Any, **kwargs: Any) -> BenchmarkResult:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _result("get_todo", ok=True, first_pass=True)

    monkeypatch.setattr(benchmark_cli, "run_benchmark", fake_run)
    output = tmp_path / "result.json"

    exit_code = main(
        [
            "run",
            "get_todo",
            str(tmp_path / "workspace"),
            "--output",
            str(output),
            "--agent-label",
            "codex",
            "--interface",
            "cli",
            "--",
            "codex",
            "exec",
            "--approve-for-me",
            "-",
        ]
    )

    assert exit_code == 0
    assert output.exists()
    kwargs = cast(dict[str, object], captured["kwargs"])
    assert kwargs["command"] == (
        "codex",
        "exec",
        "--approve-for-me",
        "-",
    )
    assert kwargs["label"] == "codex"
    assert kwargs["interface"] == "cli"
    assert '"ok": true' in capsys.readouterr().out


def test_cli_refuses_state_and_result_files_inside_the_agent_workspace(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    inside_state = workspace / "state.json"

    assert (
        main(
            [
                "prepare",
                "get_todo",
                str(workspace),
                "--state",
                str(inside_state),
                "--no-sync",
            ]
        )
        == 1
    )
    assert not workspace.exists()

    task = load_task("get_todo")
    state = prepare_workspace(
        task,
        workspace,
        tenchi_root=REPOSITORY_ROOT,
        sync=False,
    )
    inside_state.write_text(render_payload(state), encoding="utf-8")
    outside_result = tmp_path / "result.json"

    assert (
        main(
            [
                "evaluate",
                "--state",
                str(inside_state),
                "--output",
                str(outside_result),
            ]
        )
        == 1
    )
    assert not outside_result.exists()
    assert "must be stored outside" in capsys.readouterr().err


def _stub_passing_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    def agent_tests(
        changed_paths: tuple[str, ...], *args: Any, **kwargs: Any
    ) -> StepResult:
        del args, kwargs
        present = any(
            path.endswith(".py")
            and Path(path).name.startswith("test_")
            and "tests" in Path(path).parts
            for path in changed_paths
        )
        return StepResult(
            "agent_tests",
            "passed" if present else "failed",
            0 if present else None,
            0.01,
            None if present else "BENCHMARK_AGENT_TEST_MISSING",
        )

    def passing_command(*args: Any, **kwargs: Any) -> StepResult:
        del args, kwargs
        return StepResult("hidden_acceptance", "passed", 0, 0.01)

    def passing_verify(*args: Any, **kwargs: Any) -> StepResult:
        del args, kwargs
        return StepResult("tenchi_verify", "passed", 0, 0.01)

    monkeypatch.setattr(core, "_evaluate_agent_tests", agent_tests)
    monkeypatch.setattr(core, "_evaluate_command", passing_command)
    monkeypatch.setattr(core, "_evaluate_verify", passing_verify)


def _result(task: str, *, ok: bool, first_pass: bool) -> BenchmarkResult:
    status = "passed" if ok else "failed"
    exit_code = 0 if ok else 1
    interventions = 0 if first_pass else 1
    return BenchmarkResult(
        task=task,
        task_digest=f"sha256:{'b' * 64}",
        ok=ok,
        first_pass=first_pass,
        agent=AgentResult(
            label="agent",
            interface="cli",
            attempt=1,
            interventions=interventions,
            status=status,
            exit_code=exit_code,
            duration_seconds=1.0,
        ),
        source=SourceResult("a" * 40, "a" * 40, 0, ()),
        steps=tuple(
            StepResult(name, status, exit_code, 1.0)
            for name in (
                "task_integrity",
                "agent_tests",
                "hidden_acceptance",
                "tenchi_verify",
            )
        ),
    )
