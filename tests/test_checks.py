import os
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from tenchi import _checks
from tenchi._cli_results import CheckResult


class _FakeProcess:
    def __init__(self, returncode: int | None) -> None:
        self.returncode = returncode
        self.pid = 999_999
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        assert self.returncode is not None
        return self.returncode


def _always_passing(command: list[str], **kwargs: Any) -> _FakeProcess:
    del command, kwargs
    return _FakeProcess(0)


def _configured_app(root: Path) -> None:
    """Create an app root whose optional boundaries all have snapshots.

    A snapshot without its module keeps the matching check step, so these
    tests still observe every command that a fully composed application runs.
    """
    (root / "app").mkdir()
    for snapshot in ("evaluations.json", "jobs.json", "tools.json"):
        (root / snapshot).write_text("{}")


def _run(root: Path, **overrides: Any) -> CheckResult:
    arguments: dict[str, Any] = {
        "routes": "app.server.routes:api_routes",
        "title": "Example",
        "version": "0.1.0",
        "description": None,
        "snapshot": "openapi.json",
        "security_json": None,
        "timeout_seconds": 10,
    }
    arguments.update(overrides)
    return _checks.run_check(root, **arguments)


def test_check_omits_snapshot_steps_for_unconfigured_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    calls: list[list[str]] = []

    def start(command: list[str], **kwargs: Any) -> _FakeProcess:
        del kwargs
        calls.append(command)
        return _FakeProcess(0)

    monkeypatch.setattr(_checks, "_start_process", start)

    result = _run(tmp_path)

    assert result.ok is True
    assert [step.name for step in result.steps] == [
        "ruff format",
        "ruff",
        "pyright",
        "pytest",
        "doctor",
        "openapi",
    ]
    assert len(calls) == 6


@pytest.mark.parametrize(
    ("module", "snapshot", "step"),
    [
        ("app/server/tools.py", None, "tools"),
        (None, "tools.json", "tools"),
        ("app/server/jobs.py", None, "jobs"),
        (None, "jobs.json", "jobs"),
        ("app/server/evaluations.py", None, "evaluations"),
        (None, "evaluations.json", "evaluations"),
    ],
)
def test_check_keeps_a_snapshot_step_when_either_side_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: str | None,
    snapshot: str | None,
    step: str,
) -> None:
    (tmp_path / "app").mkdir()
    if module is not None:
        path = tmp_path / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    if snapshot is not None:
        (tmp_path / snapshot).write_text("{}")
    monkeypatch.setattr(_checks, "_start_process", _always_passing)

    result = _run(tmp_path)

    assert [item.name for item in result.steps] == [
        "ruff format",
        "ruff",
        "pyright",
        "pytest",
        "doctor",
        "openapi",
        step,
    ]


def test_check_never_omits_an_explicitly_overridden_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    monkeypatch.setattr(_checks, "_start_process", _always_passing)

    result = _run(tmp_path, tools="my_app.tools:tools")

    assert [item.name for item in result.steps][-1] == "tools"


def test_check_runs_every_step_and_bounds_failure_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_app(tmp_path)
    calls: list[list[str]] = []

    def start(command: list[str], **kwargs: Any) -> _FakeProcess:
        calls.append(command)
        failed = command[2:4] == ["ruff", "format"]
        kwargs["stdout_file"].write(
            ("é" * 35_000 if failed else "passed output").encode()
        )
        return _FakeProcess(1 if failed else 0)

    monkeypatch.setattr(_checks, "_start_process", start)

    result = _checks.run_check(
        tmp_path,
        routes="app.server.routes:api_routes",
        title="Example",
        version="0.1.0",
        description=None,
        snapshot="openapi.json",
        security_json=None,
        timeout_seconds=10,
    )

    assert len(calls) == 9
    assert result.ok is False
    assert [step.status for step in result.steps] == [
        "failed",
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
        "passed",
    ]
    failed = result.steps[0]
    assert failed.stdout_truncated is True
    assert len(failed.stdout.encode()) <= 65_536
    assert all(step.stdout == "" for step in result.steps[1:])


def test_check_reports_a_missing_app_without_starting_commands(tmp_path: Path) -> None:
    result = _checks.run_check(
        tmp_path,
        routes="app.server.routes:api_routes",
        title="Example",
        version="0.1.0",
        description=None,
        snapshot="openapi.json",
        security_json=None,
        timeout_seconds=10,
    )

    assert result.ok is False
    assert result.steps == ()
    assert result.error == "app/ not found; run this from an application root"


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf")])
def test_check_rejects_invalid_programmatic_timeouts(
    tmp_path: Path, timeout_seconds: float
) -> None:
    _configured_app(tmp_path)

    result = _checks.run_check(
        tmp_path,
        routes="app.server.routes:api_routes",
        title="Example",
        version="0.1.0",
        description=None,
        snapshot="openapi.json",
        security_json=None,
        timeout_seconds=timeout_seconds,
    )

    assert result.ok is False
    assert result.steps == ()
    assert result.error == "timeout_seconds must be a finite number greater than zero"


def test_check_bounds_timeout_output_after_adding_the_timeout_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_app(tmp_path)

    def start(command: list[str], **kwargs: Any) -> _FakeProcess:
        del command
        kwargs["stdout_file"].write(b"x" * 70_000)
        kwargs["stderr_file"].write(b"y" * 70_000)
        return _FakeProcess(None)

    def stop(process: _FakeProcess) -> None:
        process.returncode = -15

    monkeypatch.setattr(_checks, "_start_process", start)
    monkeypatch.setattr(_checks, "_stop_process", stop)

    result = _checks.run_check(
        tmp_path,
        routes="app.server.routes:api_routes",
        title="Example",
        version="0.1.0",
        description=None,
        snapshot="openapi.json",
        security_json=None,
        timeout_seconds=0.001,
    )

    assert len(result.steps) == 9
    assert all(step.exit_code == 124 for step in result.steps)
    assert all(len(step.stdout.encode()) <= 65_536 for step in result.steps)
    assert all(len(step.stderr.encode()) <= 65_536 for step in result.steps)
    assert all(
        step.stderr.endswith("timed out after 0.001 seconds") for step in result.steps
    )
    assert all(step.stdout_truncated for step in result.steps)
    assert all(step.stderr_truncated for step in result.steps)


def test_check_passes_description_to_the_openapi_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_app(tmp_path)
    calls: list[list[str]] = []

    def start(command: list[str], **kwargs: Any) -> _FakeProcess:
        del kwargs
        calls.append(command)
        return _FakeProcess(0)

    monkeypatch.setattr(_checks, "_start_process", start)

    result = _checks.run_check(
        tmp_path,
        routes="app.server.routes:api_routes",
        title="Example",
        version="0.1.0",
        description="Example API",
        snapshot="openapi.json",
        security_json=None,
        timeout_seconds=10,
    )

    assert result.ok is True
    assert calls[-4][-4:] == [
        "--description",
        "Example API",
        "--check",
        "openapi.json",
    ]
    assert calls[-1][-5:] == [
        "tools",
        "--tools",
        "app.server.tools:tools",
        "--check",
        "tools.json",
    ]
    assert calls[-2][-5:] == [
        "jobs",
        "--jobs",
        "app.server.jobs:jobs",
        "--check",
        "jobs.json",
    ]


def test_child_environment_prioritizes_the_current_virtualenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_app(tmp_path)
    monkeypatch.setattr(sys, "executable", "/tmp/example/.venv/bin/python")
    monkeypatch.setattr(sys, "prefix", "/tmp/example/.venv")
    monkeypatch.setattr(sys, "base_prefix", "/usr/local")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    environment = _checks._child_environment()  # pyright: ignore[reportPrivateUsage]

    assert environment["VIRTUAL_ENV"] == "/tmp/example/.venv"
    assert environment["PATH"].split(os.pathsep)[0] == "/tmp/example/.venv/bin"


def test_check_cancellation_stops_the_active_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_app(tmp_path)
    process = _FakeProcess(None)
    stopped = False
    cancellation_checks = 0

    def start(command: list[str], **kwargs: Any) -> _FakeProcess:
        del command, kwargs
        return process

    def stop(value: _FakeProcess) -> None:
        nonlocal stopped
        stopped = True
        value.returncode = -15

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks > 1

    monkeypatch.setattr(_checks, "_start_process", start)
    monkeypatch.setattr(_checks, "_stop_process", stop)

    with pytest.raises(_checks.CheckCancelled):
        _checks.run_check(
            tmp_path,
            routes="app.server.routes:api_routes",
            title="Example",
            version="0.1.0",
            description=None,
            snapshot="openapi.json",
            security_json=None,
            timeout_seconds=10,
            cancelled=cancelled,
        )

    assert stopped is True


def test_keyboard_interrupt_stops_the_active_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured_app(tmp_path)
    process = _FakeProcess(None)
    stopped = False

    def start(command: list[str], **kwargs: Any) -> _FakeProcess:
        del command, kwargs
        return process

    def stop(value: _FakeProcess) -> None:
        nonlocal stopped
        stopped = True
        value.returncode = -15

    def interrupt(seconds: float) -> None:
        del seconds
        raise KeyboardInterrupt

    monkeypatch.setattr(_checks, "_start_process", start)
    monkeypatch.setattr(_checks, "_stop_process", stop)
    monkeypatch.setattr(_checks, "sleep", interrupt)

    with pytest.raises(KeyboardInterrupt):
        _checks.run_check(
            tmp_path,
            routes="app.server.routes:api_routes",
            title="Example",
            version="0.1.0",
            description=None,
            snapshot="openapi.json",
            security_json=None,
            timeout_seconds=10,
        )

    assert stopped is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
def test_stop_process_reaps_a_child_after_its_process_group_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(None)

    def missing_group(process_id: int, sent_signal: int) -> None:
        del process_id, sent_signal
        process.returncode = 0
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", missing_group)

    _checks._stop_process(  # pyright: ignore[reportPrivateUsage]
        process  # pyright: ignore[reportArgumentType]
    )

    assert process.wait_calls == 1


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")
def test_stop_process_kills_descendants_after_the_group_leader_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _FakeProcess(None)
    signals: list[int] = []

    def signal_group(process_id: int, sent_signal: int) -> None:
        del process_id
        signals.append(sent_signal)
        if sent_signal == signal.SIGTERM:
            process.returncode = 0

    monkeypatch.setattr(os, "killpg", signal_group)

    _checks._stop_process(  # pyright: ignore[reportPrivateUsage]
        process  # pyright: ignore[reportArgumentType]
    )

    assert signals == [signal.SIGTERM, signal.SIGKILL]
