"""Repository-owned verification policy parsing and compatibility."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tenchi._openapi_operations import OperationError
from tenchi._verification_policy import (
    VERIFICATION_EVIDENCE_STAGES,
    verification_policy_comparison,
)

_STRICT_POLICY = """\
schema_version = 1

[verify]
check = true
architecture = true
openapi = true
jobs = true
tools = true
evaluations = true
"""


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")


def test_missing_policy_uses_strict_defaults(tmp_path: Path) -> None:
    (tmp_path / "placeholder").write_text("baseline\n", encoding="utf-8")
    _commit(tmp_path)

    comparison = verification_policy_comparison(tmp_path, ref="HEAD")

    assert comparison.compatible is True
    assert comparison.current.source == "default"
    assert comparison.historical.source == "default"
    assert comparison.changes == ()
    assert all(
        comparison.current.requirement(stage) == "required"
        and comparison.enforced(stage)
        for stage in VERIFICATION_EVIDENCE_STAGES
    )


def test_weakened_requirements_remain_enforced(tmp_path: Path) -> None:
    policy = tmp_path / "tenchi.toml"
    policy.write_text(_STRICT_POLICY, encoding="utf-8")
    _commit(tmp_path)
    policy.write_text(
        _STRICT_POLICY.replace("check = true", "check = false").replace(
            "evaluations = true\n",
            "",
        ),
        encoding="utf-8",
    )

    comparison = verification_policy_comparison(tmp_path, ref="HEAD")

    assert comparison.compatible is False
    assert comparison.current.requirement("check") == "disabled"
    assert comparison.current.requirement("evaluations") == "not_configured"
    assert comparison.enforced("check") is True
    assert comparison.enforced("evaluations") is True
    assert [
        (change.severity, change.stage, change.message) for change in comparison.changes
    ] == [
        (
            "breaking",
            "check",
            "required check evidence became disabled",
        ),
        (
            "breaking",
            "evaluations",
            "required evaluations evidence became not_configured",
        ),
    ]


def test_first_repository_policy_cannot_weaken_built_in_defaults(
    tmp_path: Path,
) -> None:
    (tmp_path / "placeholder").write_text("baseline\n", encoding="utf-8")
    _commit(tmp_path)
    (tmp_path / "tenchi.toml").write_text(
        _STRICT_POLICY.replace("jobs = true", "jobs = false"),
        encoding="utf-8",
    )

    comparison = verification_policy_comparison(tmp_path, ref="HEAD")

    assert comparison.current.source == "repository"
    assert comparison.historical.source == "default"
    assert comparison.compatible is False
    assert [change.severity for change in comparison.changes] == [
        "metadata",
        "breaking",
    ]
    assert comparison.enforced("jobs") is True


def test_removing_repository_policy_is_incompatible(tmp_path: Path) -> None:
    policy = tmp_path / "tenchi.toml"
    policy.write_text(_STRICT_POLICY, encoding="utf-8")
    _commit(tmp_path)
    policy.unlink()

    comparison = verification_policy_comparison(tmp_path, ref="HEAD")

    assert comparison.current.source == "default"
    assert comparison.historical.source == "repository"
    assert comparison.compatible is False
    assert comparison.changes[0].message == "repository verification policy removed"


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ("schema_version = 2\n[verify]\ncheck = true\n", "schema_version = 1"),
        ("schema_version = 1\n", "must contain a [verify] table"),
        (
            "schema_version = 1\n[verify]\nchecks = true\n",
            "unknown verify stages: checks",
        ),
        (
            'schema_version = 1\n[verify]\ncheck = "yes"\n',
            "stage 'check' must be a boolean",
        ),
        (
            "schema_version = 1\nextra = true\n[verify]\ncheck = true\n",
            "unknown top-level keys: extra",
        ),
    ],
)
def test_invalid_policy_fails_closed(
    tmp_path: Path,
    policy: str,
    message: str,
) -> None:
    (tmp_path / "tenchi.toml").write_text(policy, encoding="utf-8")

    with pytest.raises(OperationError, match=re.escape(message)):
        verification_policy_comparison(tmp_path, ref="HEAD")
