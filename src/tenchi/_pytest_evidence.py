"""Private pytest instrumentation for plan-bound execution evidence."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict

_EVIDENCE_PATH_ENVIRONMENT = "TENCHI_PYTEST_EVIDENCE_PATH"
_EVIDENCE_TARGET_ENVIRONMENT = "TENCHI_PYTEST_EVIDENCE_TARGET"
_EVIDENCE_SOURCE_ENVIRONMENT = "TENCHI_PYTEST_EVIDENCE_SOURCE"
_EVIDENCE_FIRST_LINE_ENVIRONMENT = "TENCHI_PYTEST_EVIDENCE_FIRST_LINE"
_EVIDENCE_SCHEMA_VERSION = 1
_MAX_EVIDENCE_BYTES = 16_384
_MAX_COUNT = 9_007_199_254_740_991

type TestExecutionStatus = Literal[
    "passed",
    "not_collected",
    "deselected",
    "skipped",
    "xfailed",
    "xpassed",
    "failed",
    "errored",
    "ambiguous",
    "receipt_missing",
    "receipt_invalid",
]
type _Count = Annotated[int, Field(ge=0, le=_MAX_COUNT)]


class TestExecutionEvidencePayload(TypedDict):
    target: str
    provenance: Literal["project_pytest_process"]
    status: TestExecutionStatus
    collected: _Count
    passed: _Count
    skipped: _Count
    xfailed: _Count
    xpassed: _Count
    failed: _Count
    errored: _Count
    deselected: _Count
    ambiguous: _Count


@with_config(ConfigDict(extra="forbid"))
class _EvidencePayload(TypedDict):
    schema_version: Literal[1]
    target: str
    collected: _Count
    passed: _Count
    skipped: _Count
    xfailed: _Count
    xpassed: _Count
    failed: _Count
    errored: _Count
    deselected: _Count
    ambiguous: _Count


_EVIDENCE_ADAPTER = TypeAdapter(_EvidencePayload)


@dataclass(frozen=True, slots=True)
class TestDefinitionIdentity:
    """Source identity of the exact test function accepted by verification."""

    source: Path
    first_line: int

    def __post_init__(self) -> None:
        if self.first_line < 1:
            raise ValueError("first_line must be greater than zero")


@dataclass(frozen=True, slots=True)
class TestExecutionEvidence:
    """Payload-safe pytest evidence for one exact planned test target."""

    target: str
    status: TestExecutionStatus
    collected: int
    passed: int
    skipped: int
    xfailed: int
    xpassed: int
    failed: int
    errored: int
    deselected: int
    ambiguous: int
    provenance: Literal["project_pytest_process"] = "project_pytest_process"

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> TestExecutionEvidencePayload:
        return {
            "target": self.target,
            "provenance": self.provenance,
            "status": self.status,
            "collected": self.collected,
            "passed": self.passed,
            "skipped": self.skipped,
            "xfailed": self.xfailed,
            "xpassed": self.xpassed,
            "failed": self.failed,
            "errored": self.errored,
            "deselected": self.deselected,
            "ambiguous": self.ambiguous,
        }


def evidence_environment(
    path: Path,
    target: str,
    identity: TestDefinitionIdentity | None,
) -> dict[str, str]:
    """Return the private child environment used by the pytest plugin."""
    environment = {
        _EVIDENCE_PATH_ENVIRONMENT: str(path),
        _EVIDENCE_TARGET_ENVIRONMENT: target,
        # Inherited pytest selection and import hooks would make verification
        # depend on the invoking shell rather than the checked project. This
        # does not turn project-owned pytest code into a security boundary;
        # the receipt reports that provenance explicitly.
        "PYTEST_ADDOPTS": "",
        "PYTEST_PLUGINS": "",
        "PYTHONPATH": "",
    }
    if identity is not None:
        environment.update(
            {
                _EVIDENCE_SOURCE_ENVIRONMENT: str(identity.source.resolve()),
                _EVIDENCE_FIRST_LINE_ENVIRONMENT: str(identity.first_line),
            }
        )
    return environment


def read_test_execution_evidence(path: Path, target: str) -> TestExecutionEvidence:
    """Read one bounded plugin receipt and bind it to the requested target."""
    try:
        encoded = _read_bounded(path)
    except OSError:
        return unavailable_test_execution_evidence(target, "receipt_missing")
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        return unavailable_test_execution_evidence(target, "receipt_invalid")
    try:
        raw: object = json.loads(encoded.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError
        payload = _EVIDENCE_ADAPTER.validate_python(raw, strict=True)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
        return unavailable_test_execution_evidence(target, "receipt_invalid")
    if payload != raw or payload["target"] != target:
        return unavailable_test_execution_evidence(target, "receipt_invalid")
    categorized = sum(
        payload[key]
        for key in (
            "passed",
            "skipped",
            "xfailed",
            "xpassed",
            "failed",
            "errored",
            "ambiguous",
        )
    )
    if categorized != payload["collected"]:
        return unavailable_test_execution_evidence(target, "receipt_invalid")

    status = _execution_status(payload)
    return TestExecutionEvidence(
        target=target,
        status=status,
        collected=payload["collected"],
        passed=payload["passed"],
        skipped=payload["skipped"],
        xfailed=payload["xfailed"],
        xpassed=payload["xpassed"],
        failed=payload["failed"],
        errored=payload["errored"],
        deselected=payload["deselected"],
        ambiguous=payload["ambiguous"],
    )


def _execution_status(payload: _EvidencePayload) -> TestExecutionStatus:
    if payload["collected"] == 0:
        return "deselected" if payload["deselected"] else "not_collected"
    if payload["ambiguous"]:
        return "ambiguous"
    if payload["errored"]:
        return "errored"
    if payload["failed"]:
        return "failed"
    if payload["xpassed"]:
        return "xpassed"
    if payload["xfailed"]:
        return "xfailed"
    if payload["skipped"]:
        return "skipped"
    if payload["deselected"]:
        return "deselected"
    if payload["passed"] == payload["collected"]:
        return "passed"
    return "ambiguous"


def unavailable_test_execution_evidence(
    target: str,
    status: Literal["receipt_missing", "receipt_invalid"],
) -> TestExecutionEvidence:
    return TestExecutionEvidence(
        target=target,
        status=status,
        collected=0,
        passed=0,
        skipped=0,
        xfailed=0,
        xpassed=0,
        failed=0,
        errored=0,
        deselected=0,
        ambiguous=0,
    )


@dataclass(slots=True)
class _Report:
    when: str
    outcome: str
    was_xfail: bool


@dataclass(slots=True)
class _PluginState:
    path: Path
    target: str
    identity: TestDefinitionIdentity | None
    collected: set[str] = field(default_factory=lambda: set[str]())
    identity_mismatched: set[str] = field(default_factory=lambda: set[str]())
    deselected: set[str] = field(default_factory=lambda: set[str]())
    reports: dict[str, list[_Report]] = field(
        default_factory=lambda: dict[str, list[_Report]]()
    )


_plugin_state: _PluginState | None = None


def pytest_configure() -> None:
    """Initialize evidence capture only when Tenchi supplied both settings."""
    global _plugin_state
    path = os.environ.get(_EVIDENCE_PATH_ENVIRONMENT)
    target = os.environ.get(_EVIDENCE_TARGET_ENVIRONMENT)
    source = os.environ.get(_EVIDENCE_SOURCE_ENVIRONMENT)
    raw_first_line = os.environ.get(_EVIDENCE_FIRST_LINE_ENVIRONMENT)
    identity: TestDefinitionIdentity | None = None
    if source is not None or raw_first_line is not None:
        if source is None or raw_first_line is None:
            _plugin_state = None
            return
        try:
            identity = TestDefinitionIdentity(
                source=Path(source).resolve(),
                first_line=int(raw_first_line),
            )
        except (OSError, ValueError):
            _plugin_state = None
            return
    _plugin_state = (
        _PluginState(path=Path(path), target=target, identity=identity)
        if path is not None and target is not None
        else None
    )


def pytest_collection_finish(session: object) -> None:
    """Capture the final collection after selection plugins have run."""
    if _plugin_state is None:
        return
    items = getattr(session, "items", ())
    for item in items:
        nodeid = getattr(item, "nodeid", None)
        if not isinstance(nodeid, str) or not _matches_target(
            nodeid, _plugin_state.target
        ):
            continue
        _plugin_state.collected.add(nodeid)
        if _plugin_state.identity is not None and not _callable_matches_identity(
            item, _plugin_state.identity
        ):
            _plugin_state.identity_mismatched.add(nodeid)


def pytest_deselected(items: list[object]) -> None:
    """Record target invocations removed by selection or another plugin."""
    if _plugin_state is None:
        return
    for item in items:
        nodeid = getattr(item, "nodeid", None)
        if isinstance(nodeid, str) and _matches_target(nodeid, _plugin_state.target):
            _plugin_state.deselected.add(nodeid)


def pytest_runtest_logreport(report: object) -> None:
    """Retain outcome categories without parameter IDs or failure details."""
    if _plugin_state is None:
        return
    nodeid = getattr(report, "nodeid", None)
    when = getattr(report, "when", None)
    outcome = getattr(report, "outcome", None)
    if (
        not isinstance(nodeid, str)
        or not _matches_target(nodeid, _plugin_state.target)
        or when not in {"setup", "call", "teardown"}
        or outcome not in {"passed", "failed", "skipped"}
    ):
        return
    _plugin_state.reports.setdefault(nodeid, []).append(
        _Report(
            when=when,
            outcome=outcome,
            was_xfail=hasattr(report, "wasxfail"),
        )
    )


def pytest_sessionfinish() -> None:
    """Write one bounded receipt after all target lifecycle reports exist."""
    if _plugin_state is None:
        return
    counts = {
        "passed": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "failed": 0,
        "errored": 0,
        "ambiguous": 0,
    }
    for nodeid in _plugin_state.collected:
        category = (
            "ambiguous"
            if nodeid in _plugin_state.identity_mismatched
            else _classify_reports(_plugin_state.reports.get(nodeid, []))
        )
        counts[category] += 1
    payload: _EvidencePayload = {
        "schema_version": _EVIDENCE_SCHEMA_VERSION,
        "target": _plugin_state.target,
        "collected": len(_plugin_state.collected),
        "passed": counts["passed"],
        "skipped": counts["skipped"],
        "xfailed": counts["xfailed"],
        "xpassed": counts["xpassed"],
        "failed": counts["failed"],
        "errored": counts["errored"],
        "deselected": len(_plugin_state.deselected),
        "ambiguous": counts["ambiguous"],
    }
    try:
        _write_receipt(
            _plugin_state.path,
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
    except OSError:
        return


def _read_bounded(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        chunks: list[bytes] = []
        remaining = _MAX_EVIDENCE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_receipt(path: Path, encoded: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written == 0:
                raise OSError("could not write pytest evidence")
            view = view[written:]
    finally:
        os.close(descriptor)


def _matches_target(nodeid: str, target: str) -> bool:
    normalized = nodeid.replace("\\", "/")
    return normalized == target or normalized.startswith(f"{target}[")


def _callable_matches_identity(
    item: object,
    identity: TestDefinitionIdentity,
) -> bool:
    function = getattr(item, "obj", None)
    code = getattr(function, "__code__", None)
    filename = getattr(code, "co_filename", None)
    first_line = getattr(code, "co_firstlineno", None)
    if not isinstance(filename, str) or not isinstance(first_line, int):
        return False
    try:
        source = Path(filename).resolve()
    except (OSError, RuntimeError):
        return False
    return source == identity.source and first_line == identity.first_line


def _classify_reports(
    reports: list[_Report],
) -> Literal[
    "passed",
    "skipped",
    "xfailed",
    "xpassed",
    "failed",
    "errored",
    "ambiguous",
]:
    phase_counts = {
        phase: sum(report.when == phase for report in reports)
        for phase in ("setup", "call", "teardown")
    }
    if any(count > 1 for count in phase_counts.values()):
        return "ambiguous"
    xfail_reports = [report for report in reports if report.was_xfail]
    if xfail_reports:
        if any(report.outcome == "skipped" for report in xfail_reports):
            return "xfailed"
        return "xpassed"
    if any(report.outcome == "skipped" for report in reports):
        return "skipped"
    failed = [report for report in reports if report.outcome == "failed"]
    if failed:
        return (
            "failed" if all(report.when == "call" for report in failed) else "errored"
        )
    phases = [report.when for report in reports]
    if sorted(phases) != ["call", "setup", "teardown"]:
        return "ambiguous"
    if not all(report.outcome == "passed" for report in reports):
        return "ambiguous"
    return "passed"


__all__ = [
    "TestDefinitionIdentity",
    "TestExecutionEvidence",
    "TestExecutionEvidencePayload",
    "TestExecutionStatus",
    "evidence_environment",
    "read_test_execution_evidence",
    "unavailable_test_execution_evidence",
]
