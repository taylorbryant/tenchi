"""Renderer-independent background-job manifest operations."""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from typing_extensions import TypedDict

from ._cli_results import AGENT_PROTOCOL_VERSION, AgentProtocolVersion
from ._openapi_operations import (
    OperationError,
    isolated_project_imports,
    project_path,
    read_git_snapshot,
)
from ._schema_compatibility import ChangeSeverity
from .compatibility import (
    CompatibilityChange,
    CompatibilityReport,
    CompatibilityStatus,
    analyze_job_compatibility,
)
from .jobs import JOB_MANIFEST_VERSION, JobGroup, JobManifest, job_manifest

_EMPTY_JOB_MANIFEST = json.dumps({"schema_version": JOB_MANIFEST_VERSION, "jobs": []})


class JobListPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    manifest: JobManifest


class JobChangePayload(TypedDict):
    severity: ChangeSeverity
    location: str
    message: str


class JobCountsPayload(TypedDict):
    breaking: int
    additive: int
    metadata: int
    unknown: int


class JobDiffPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    baseline: str
    status: CompatibilityStatus
    compatible: bool
    counts: JobCountsPayload
    changes: list[JobChangePayload]


@dataclass(frozen=True, slots=True)
class JobListResult:
    """Versioned registered-job result shared by the CLI and MCP server."""

    root: str
    manifest: JobManifest
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> JobListPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "manifest": self.manifest,
        }


@dataclass(frozen=True, slots=True)
class JobDiffResult:
    """Versioned job-message compatibility result shared by CLI and MCP."""

    root: str
    baseline: str
    report: CompatibilityReport
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> JobDiffPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "baseline": self.baseline,
            "status": self.report.status,
            "compatible": self.report.compatible,
            "counts": {
                "breaking": self.report.count("breaking"),
                "additive": self.report.count("additive"),
                "metadata": self.report.count("metadata"),
                "unknown": self.report.count("unknown"),
            },
            "changes": [
                {
                    "severity": change.severity,
                    "location": change.location,
                    "message": change.message,
                }
                for change in self.report.changes
            ],
        }


def load_job_group(root: Path, target: str) -> JobGroup:
    """Import *target* from *root* and return its registered job group."""
    resolved_root = root.resolve()
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise OperationError(f"expected module:attribute, got {target!r}")

    with isolated_project_imports(resolved_root, module_names=(module_name,)):
        root_string = str(resolved_root)
        if root_string in sys.path:
            sys.path.remove(root_string)
        sys.path.insert(0, root_string)
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise OperationError(f"could not import {module_name!r}: {exc}") from exc
        if not hasattr(module, attribute):
            raise OperationError(
                f"module {module_name!r} has no attribute {attribute!r}"
            )
        group = getattr(module, attribute)
        if not isinstance(group, JobGroup):
            raise OperationError(
                f"{target!r} is not a tenchi JobGroup (got {type(group).__name__})"
            )
        return group


def job_list_result(root: Path, group: JobGroup) -> JobListResult:
    """Return the registered durable job-message manifest for *group*."""
    return JobListResult(
        root=str(root.resolve()),
        manifest=job_manifest(group),
    )


def job_diff_result(
    root: Path,
    *,
    jobs: str,
    snapshot: Path,
    ref: str | None,
    allow_missing_baseline: bool = False,
) -> JobDiffResult:
    """Generate the job manifest and compare it with a baseline."""
    if allow_missing_baseline and ref is None:
        raise OperationError("allow_missing_baseline requires a Git ref")
    resolved_root = root.resolve()
    current = job_manifest(load_job_group(resolved_root, jobs))
    if ref is None:
        if snapshot.is_absolute():
            baseline_path = snapshot.resolve()
            try:
                baseline_path.relative_to(resolved_root)
            except ValueError as exc:
                raise OperationError(
                    "snapshot path must stay inside the application root"
                ) from exc
        else:
            baseline_path = project_path(resolved_root, str(snapshot))
        try:
            baseline_text = baseline_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise OperationError(
                f"could not read baseline {str(snapshot)!r}: {exc}"
            ) from exc
        baseline_label = str(snapshot)
        baseline_present = True
    else:
        baseline = read_git_snapshot(
            resolved_root,
            ref=ref,
            snapshot=snapshot,
            missing_text=_EMPTY_JOB_MANIFEST if allow_missing_baseline else None,
        )
        baseline_text = baseline.text
        baseline_label = baseline.label
        baseline_present = baseline.present
    return compare_job_baseline(
        resolved_root,
        baseline_text=baseline_text,
        baseline_label=baseline_label,
        current=current,
        baseline_present=baseline_present,
    )


def compare_job_baseline(
    root: Path,
    *,
    baseline_text: str,
    baseline_label: str,
    current: JobManifest,
    baseline_present: bool = True,
) -> JobDiffResult:
    """Compare a generated job-message manifest with serialized JSON."""
    try:
        baseline: object = json.loads(baseline_text)
    except json.JSONDecodeError as exc:
        raise OperationError(
            f"baseline {baseline_label!r} is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    except ValueError as exc:
        raise OperationError(
            f"baseline {baseline_label!r} is not valid JSON ({exc})"
        ) from exc
    try:
        report = analyze_job_compatibility(baseline, current)
    except ValueError as exc:
        raise OperationError(
            f"could not compare baseline {baseline_label!r}: {exc}"
        ) from exc
    if not baseline_present:
        report = CompatibilityReport(
            changes=(
                *report.changes,
                CompatibilityChange(
                    severity="metadata",
                    location="job manifest baseline",
                    message=(
                        "historical baseline absent; explicit first-adoption "
                        "override used"
                    ),
                ),
            )
        )
    return JobDiffResult(
        root=str(root.resolve()),
        baseline=baseline_label,
        report=report,
    )
