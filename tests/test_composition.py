import subprocess
from pathlib import Path

import pytest

from tenchi._composition import load_optional_groups, optional_targets_absent_at_ref
from tenchi._openapi_operations import OperationError
from tenchi._targets import DEFAULT_JOBS_TARGET, DEFAULT_TOOLS_TARGET


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(root: Path, relative: str, content: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_absence_at_a_ref_is_decided_by_the_baseline_tree(tmp_path: Path) -> None:
    _write(tmp_path, "app/server/tools/__init__.py", "tools = None\n")
    _write(tmp_path, "app/server/jobs.py", "jobs = None\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "baseline")
    # The working tree no longer has jobs, but the baseline does.
    (tmp_path / "app/server/jobs.py").unlink()

    absent = optional_targets_absent_at_ref(
        tmp_path,
        ref="HEAD",
        targets={
            "tools": (DEFAULT_TOOLS_TARGET, DEFAULT_TOOLS_TARGET),
            "jobs": (DEFAULT_JOBS_TARGET, DEFAULT_JOBS_TARGET),
            "evaluations": (
                "app.server.evaluations:runner",
                "app.server.evaluations:runner",
            ),
            "custom": ("custom.tools:tools", DEFAULT_TOOLS_TARGET),
        },
    )

    assert absent == frozenset({"evaluations"})
    assert optional_targets_absent_at_ref(tmp_path, ref="HEAD", targets={}) == (
        frozenset()
    )


def test_absence_at_a_ref_requires_a_repository(tmp_path: Path) -> None:
    with pytest.raises(OperationError):
        optional_targets_absent_at_ref(
            tmp_path,
            ref="HEAD",
            targets={"tools": (DEFAULT_TOOLS_TARGET, DEFAULT_TOOLS_TARGET)},
        )


def test_load_optional_groups_loads_present_modules_only(tmp_path: Path) -> None:
    for package in ("app", "app/server"):
        _write(tmp_path, f"{package}/__init__.py")
    _write(
        tmp_path,
        "app/server/tools/__init__.py",
        "from tenchi.tools import tool_group\n\ntools = tool_group()\n",
    )
    _write(
        tmp_path,
        "app/server/jobs.py",
        "from tenchi.jobs import job_group\n\njobs = job_group()\n",
    )

    groups = load_optional_groups(
        tmp_path,
        tasks="app.server.tasks:runner",
        jobs="app.server.jobs:jobs",
        tools="app.server.tools:tools",
        evaluations="app.server.evaluations:runner",
    )

    assert groups.tasks is None
    assert groups.evaluations is None
    assert groups.jobs is not None
    assert groups.tools is not None


def test_load_optional_groups_fails_loudly_for_an_overridden_target(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()

    with pytest.raises(OperationError):
        load_optional_groups(
            tmp_path,
            tasks="app.server.tasks:runner",
            jobs="app.server.jobs:jobs",
            tools="missing.tools:tools",
            evaluations="app.server.evaluations:runner",
        )
