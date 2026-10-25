"""Execute the generated CI gate against historical Git snapshots."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from tenchi.scaffold import app_files


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            *arguments,
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _app(tmp_path: Path) -> Path:
    root = tmp_path / "application"
    for relative, content in app_files("application").items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(root, "init", "-q")
    return root


def _run_gate(
    root: Path, *, event: str, baseline: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "EVENT_NAME: ${{ github.event_name }}" in workflow
    assert (
        "BASE_SHA: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.base.sha || github.event.before }}"
    ) in workflow
    script = dedent(workflow.split("        run: |\n", 1)[1])
    executable = root.parent / "bin"
    executable.mkdir(exist_ok=True)
    log = root.parent / "invocation.json"
    # Stand in for uv using the test environment; run the actual Tenchi CLI
    # and its check subprocesses, while retaining the generated shell gate.
    (executable / "uv").write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(sys.executable)} "
        f'{shlex.quote(str(executable / "uv.py"))} "$@"\n',
        encoding="utf-8",
    )
    (executable / "uv").chmod(0o755)
    (executable / "uv.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:3] == ['run', 'tenchi']\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv[3:]))\n"
        "os.execv(sys.executable, [sys.executable, '-m', 'tenchi.cli',\n"
        "                        *sys.argv[3:], '--json'])\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=root,
        env={
            **os.environ,
            "PATH": f"{executable}{os.pathsep}{os.environ['PATH']}",
            "EVENT_NAME": event,
            "BASE_SHA": baseline,
        },
        capture_output=True,
        text=True,
    )
    return result, json.loads(log.read_text()) if log.exists() else []


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_generated_ci_rejects_breakage_even_after_snapshot_replacement(
    tmp_path: Path, event: str
) -> None:
    root = _app(tmp_path)
    snapshot = root / "openapi.json"
    current = snapshot.read_text(encoding="utf-8")
    historical = json.loads(current)
    historical["paths"]["/legacy"] = historical["paths"]["/todos"]
    snapshot.write_text(json.dumps(historical), encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "historical API")
    baseline = _git(root, "rev-parse", "HEAD")
    snapshot.write_text(current, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "remove legacy API and replace snapshot")
    (root / "README.md").write_text("Updated documentation\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "second commit in the same push")

    result, invocation = _run_gate(root, event=event, baseline=baseline)

    assert invocation == ["verify", "--base-ref", baseline]
    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["check"]["ok"] is True
    assert receipt["baseline"]["commit"] == baseline
    assert receipt["openapi"]["compatible"] is False
    assert receipt["ok"] is False


def test_generated_ci_initial_push_runs_checks_without_claiming_compatibility(
    tmp_path: Path,
) -> None:
    root = _app(tmp_path)

    result, invocation = _run_gate(root, event="push", baseline="0" * 40)

    assert result.returncode == 0, result.stdout + result.stderr
    assert invocation == ["check"]
    notice, payload = result.stdout.split("\n", 1)
    assert "no historical baseline" in notice
    assert json.loads(payload)["ok"] is True


@pytest.mark.parametrize("baseline", ["", "1" * 40])
def test_generated_ci_unavailable_baseline_fails_closed(
    tmp_path: Path, baseline: str
) -> None:
    root = _app(tmp_path)

    result, invocation = _run_gate(root, event="push", baseline=baseline)

    assert result.returncode != 0
    assert invocation == []
