"""Renderer-independent application-tool snapshot operations."""

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
    CompatibilityReport,
    CompatibilityStatus,
    analyze_tool_compatibility,
)
from .tools import ToolGroup, ToolManifest, tool_manifest


class ToolListPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    manifest: ToolManifest


class ToolChangePayload(TypedDict):
    severity: ChangeSeverity
    location: str
    message: str


class ToolCountsPayload(TypedDict):
    breaking: int
    additive: int
    metadata: int
    unknown: int


class ToolDiffPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    baseline: str
    status: CompatibilityStatus
    compatible: bool
    counts: ToolCountsPayload
    changes: list[ToolChangePayload]


@dataclass(frozen=True, slots=True)
class ToolListResult:
    """Versioned registered-tool result shared by the CLI and MCP server."""

    root: str
    manifest: ToolManifest
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> ToolListPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "manifest": self.manifest,
        }


@dataclass(frozen=True, slots=True)
class ToolDiffResult:
    """Versioned tool compatibility result shared by CLI and MCP."""

    root: str
    baseline: str
    report: CompatibilityReport
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> ToolDiffPayload:
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


def load_tool_group(root: Path, target: str) -> ToolGroup:
    """Import *target* from *root* and return its Tenchi tool group."""
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
        group: object = getattr(module, attribute)
        if not isinstance(group, ToolGroup):
            raise OperationError(
                f"{target!r} is not a tenchi ToolGroup (got {type(group).__name__})"
            )
        return group


def tool_list_result(root: Path, group: ToolGroup) -> ToolListResult:
    """Return the registered application-tool manifest for *group*."""
    return ToolListResult(
        root=str(root.resolve()),
        manifest=tool_manifest(group),
    )


def tool_diff_result(
    root: Path,
    *,
    tools: str,
    snapshot: Path,
    ref: str | None,
) -> ToolDiffResult:
    """Generate the tool manifest and compare it with a baseline."""
    resolved_root = root.resolve()
    current = tool_manifest(load_tool_group(resolved_root, tools))
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
    else:
        baseline = read_git_snapshot(
            resolved_root,
            ref=ref,
            snapshot=snapshot,
        )
        baseline_text = baseline.text
        baseline_label = baseline.label
    return compare_tool_baseline(
        resolved_root,
        baseline_text=baseline_text,
        baseline_label=baseline_label,
        current=current,
    )


def compare_tool_baseline(
    root: Path,
    *,
    baseline_text: str,
    baseline_label: str,
    current: ToolManifest,
) -> ToolDiffResult:
    """Compare a generated application-tool manifest with serialized JSON."""
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
        report = analyze_tool_compatibility(baseline, current)
    except ValueError as exc:
        raise OperationError(
            f"could not compare baseline {baseline_label!r}: {exc}"
        ) from exc
    return ToolDiffResult(
        root=str(root.resolve()),
        baseline=baseline_label,
        report=report,
    )
