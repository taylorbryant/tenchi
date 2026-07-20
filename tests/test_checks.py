import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tenchi import _checks


def test_check_runs_every_step_and_bounds_failure_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        failed = command[2:4] == ["ruff", "format"]
        kwargs["stdout"].write(("é" * 35_000 if failed else "passed output").encode())
        return subprocess.CompletedProcess(
            command,
            1 if failed else 0,
        )

    monkeypatch.setattr(subprocess, "run", run)

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

    assert len(calls) == 6
    assert result.ok is False
    assert [step.status for step in result.steps] == [
        "failed",
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
    (tmp_path / "app").mkdir()

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
    (tmp_path / "app").mkdir()

    def timeout(
        command: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[bytes]:
        kwargs["stdout"].write(b"x" * 70_000)
        kwargs["stderr"].write(b"y" * 70_000)
        raise subprocess.TimeoutExpired(
            command,
            timeout=10,
        )

    monkeypatch.setattr(subprocess, "run", timeout)

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

    assert len(result.steps) == 6
    assert all(step.exit_code == 124 for step in result.steps)
    assert all(len(step.stdout.encode()) <= 65_536 for step in result.steps)
    assert all(len(step.stderr.encode()) <= 65_536 for step in result.steps)
    assert all(
        step.stderr.endswith("timed out after 10 seconds") for step in result.steps
    )
    assert all(step.stdout_truncated for step in result.steps)
    assert all(step.stderr_truncated for step in result.steps)


def test_check_passes_description_to_the_openapi_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)

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
    assert calls[-1][-4:] == ["--description", "Example API", "--check", "openapi.json"]


def test_child_environment_prioritizes_the_current_virtualenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app").mkdir()
    environments: list[dict[str, str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        del command
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(sys, "executable", "/tmp/example/.venv/bin/python")
    monkeypatch.setattr(sys, "prefix", "/tmp/example/.venv")
    monkeypatch.setattr(sys, "base_prefix", "/usr/local")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

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

    assert result.ok is True
    assert len(environments) == 6
    assert all(
        environment["VIRTUAL_ENV"] == "/tmp/example/.venv"
        for environment in environments
    )
    assert all(
        environment["PATH"].split(os.pathsep)[0] == "/tmp/example/.venv/bin"
        for environment in environments
    )
