import ast
import json
from pathlib import Path

import pytest

from tenchi import _pytest_evidence
from tenchi._checks import run_test_execution
from tenchi._pytest_evidence import TestDefinitionIdentity as _TestDefinitionIdentity

_TARGET = "app/features/projects/tests/test_create_project.py::test_create_project"


def _run(tmp_path: Path, source: str):
    (tmp_path / "app").mkdir()
    test_path = tmp_path / _TARGET.partition("::")[0]
    test_path.parent.mkdir(parents=True)
    test_path.write_text(source, encoding="utf-8")
    functions = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == "test_create_project"
    ]
    function = functions[0] if functions else None
    first_line = (
        min([function.lineno, *(item.lineno for item in function.decorator_list)])
        if function is not None
        else 1
    )
    return run_test_execution(
        tmp_path,
        target=_TARGET,
        identity=_TestDefinitionIdentity(test_path.resolve(), first_line),
        timeout_seconds=10,
    )


def test_execution_evidence_requires_every_parameter_to_pass(tmp_path: Path) -> None:
    evidence = _run(
        tmp_path,
        """\
import pytest


@pytest.mark.parametrize("value", [1, 2])
def test_create_project(value: int) -> None:
    assert value > 0
""",
    )

    assert evidence.status == "passed"
    assert evidence.collected == 2
    assert evidence.passed == 2
    assert evidence.ok is True


def test_execution_evidence_rejects_a_replacement_callable(tmp_path: Path) -> None:
    evidence = _run(
        tmp_path,
        """\
from collections.abc import Callable


def replace_test(function: Callable[[], None]) -> Callable[[], None]:
    del function

    def replacement() -> None:
        pass

    return replacement


@replace_test
def test_create_project() -> None:
    raise AssertionError("the planned function must run")
""",
    )

    assert evidence.status == "ambiguous"
    assert evidence.collected == 1
    assert evidence.passed == 0
    assert evidence.ambiguous == 1


@pytest.mark.parametrize(
    ("source", "status"),
    [
        (
            """\
import pytest


@pytest.mark.skip(reason="not ready")
def test_create_project() -> None:
    pass
""",
            "skipped",
        ),
        (
            """\
import pytest


@pytest.mark.xfail(reason="not ready")
def test_create_project() -> None:
    assert False
""",
            "xfailed",
        ),
        (
            """\
import pytest


@pytest.mark.xfail(reason="not ready")
def test_create_project() -> None:
    pass
""",
            "xpassed",
        ),
        (
            """\
def test_create_project() -> None:
    assert False
""",
            "failed",
        ),
        (
            """\
import pytest


@pytest.fixture
def broken() -> None:
    raise RuntimeError("setup failed")


def test_create_project(broken: None) -> None:
    pass
""",
            "errored",
        ),
        (
            """\
import pytest


@pytest.mark.parametrize("value", [])
def test_create_project(value: int) -> None:
    pass
""",
            "skipped",
        ),
    ],
)
def test_execution_evidence_rejects_nonpassing_outcomes(
    tmp_path: Path,
    source: str,
    status: str,
) -> None:
    evidence = _run(tmp_path, source)

    assert evidence.status == status
    assert evidence.ok is False


def test_execution_evidence_reports_an_uncollected_target(tmp_path: Path) -> None:
    evidence = _run(
        tmp_path,
        """\
def test_something_else() -> None:
    pass
""",
    )

    assert evidence.status == "not_collected"
    assert evidence.collected == 0


def test_execution_evidence_reports_a_deselected_target(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -k something_else\n",
        encoding="utf-8",
    )

    evidence = _run(
        tmp_path,
        """\
def test_create_project() -> None:
    pass
""",
    )

    assert evidence.status == "deselected"
    assert evidence.deselected == 1


def test_execution_evidence_rejects_repeated_lifecycle_reports() -> None:
    reports = [
        _pytest_evidence._Report(  # pyright: ignore[reportPrivateUsage]
            when="setup", outcome="passed", was_xfail=False
        ),
        _pytest_evidence._Report(  # pyright: ignore[reportPrivateUsage]
            when="call", outcome="failed", was_xfail=False
        ),
        _pytest_evidence._Report(  # pyright: ignore[reportPrivateUsage]
            when="teardown", outcome="passed", was_xfail=False
        ),
        _pytest_evidence._Report(  # pyright: ignore[reportPrivateUsage]
            when="setup", outcome="passed", was_xfail=False
        ),
        _pytest_evidence._Report(  # pyright: ignore[reportPrivateUsage]
            when="call", outcome="passed", was_xfail=False
        ),
        _pytest_evidence._Report(  # pyright: ignore[reportPrivateUsage]
            when="teardown", outcome="passed", was_xfail=False
        ),
    ]

    assert (
        _pytest_evidence._classify_reports(  # pyright: ignore[reportPrivateUsage]
            reports
        )
        == "ambiguous"
    )


def test_execution_evidence_rejects_inconsistent_counts(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": _TARGET,
                "collected": 1,
                "passed": 1,
                "skipped": 1,
                "xfailed": 0,
                "xpassed": 0,
                "failed": 0,
                "errored": 0,
                "deselected": 0,
                "ambiguous": 0,
            }
        ),
        encoding="utf-8",
    )

    evidence = _pytest_evidence.read_test_execution_evidence(path, _TARGET)

    assert evidence.status == "receipt_invalid"


def test_execution_evidence_bounds_the_private_receipt(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_bytes(b"{" + (b"x" * 20_000) + b"}")

    evidence = _pytest_evidence.read_test_execution_evidence(path, _TARGET)

    assert evidence.status == "receipt_invalid"


def test_targeted_execution_rejects_a_failed_pytest_session(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(
        """\
def pytest_sessionfinish(session, exitstatus) -> None:
    del exitstatus
    session.exitstatus = 1
""",
        encoding="utf-8",
    )

    evidence = _run(
        tmp_path,
        """\
def test_create_project() -> None:
    pass
""",
    )

    assert evidence.status == "ambiguous"
    assert evidence.collected == 1
    assert evidence.passed == 0
    assert evidence.ambiguous == 1
