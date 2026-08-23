"""Renderer-independent evaluation discovery and execution."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from typing_extensions import TypedDict

from ._cli_results import (
    AGENT_PROTOCOL_VERSION,
    AgentProtocolVersion,
    EvaluationBudgetResult,
    EvaluationCaseResult,
    EvaluationEntryResult,
    EvaluationListResult,
    EvaluationMetricDeclarationResult,
    EvaluationMetricResult,
    EvaluationOutcomeResult,
    EvaluationRunErrorResult,
    EvaluationRunResult,
)
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
    analyze_evaluation_compatibility,
)
from .evaluations import (
    EVALUATION_MANIFEST_VERSION,
    EvaluationManifest,
    EvaluationNotFoundError,
    EvaluationRunner,
    evaluation_manifest,
)

logger = logging.getLogger("tenchi.evaluations")


class EvaluationChangePayload(TypedDict):
    severity: ChangeSeverity
    location: str
    message: str


class EvaluationCountsPayload(TypedDict):
    breaking: int
    additive: int
    metadata: int
    unknown: int


class EvaluationDiffPayload(TypedDict):
    schema_version: AgentProtocolVersion
    root: str
    baseline: str
    status: CompatibilityStatus
    compatible: bool
    counts: EvaluationCountsPayload
    changes: list[EvaluationChangePayload]


@dataclass(frozen=True, slots=True)
class EvaluationDiffResult:
    """Versioned evaluation-policy compatibility result shared by CLI and MCP."""

    root: str
    baseline: str
    report: CompatibilityReport
    schema_version: AgentProtocolVersion = AGENT_PROTOCOL_VERSION

    def as_dict(self) -> EvaluationDiffPayload:
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


_EMPTY_EVALUATION_MANIFEST = json.dumps(
    {
        "schema_version": EVALUATION_MANIFEST_VERSION,
        "evaluations": [],
    },
    sort_keys=True,
)


def load_evaluation_runner(root: Path, target: str) -> EvaluationRunner:
    """Import *target* from *root* and return its evaluation runner."""
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
            raise OperationError(
                f"could not import {module_name!r} ({type(exc).__name__})"
            ) from exc
        try:
            runner = getattr(module, attribute)
        except AttributeError:
            raise OperationError(
                f"module {module_name!r} has no attribute {attribute!r}"
            ) from None
        except Exception as exc:
            raise OperationError(
                f"could not read {target!r} ({type(exc).__name__})"
            ) from exc
        if not isinstance(runner, EvaluationRunner):
            raise OperationError(
                f"{target!r} is not a tenchi EvaluationRunner "
                f"(got {type(runner).__name__})"
            )
        return runner


@contextmanager
def discard_evaluation_output() -> Generator[None, None, None]:
    """Discard direct output so prompts and model results cannot cross adapters."""
    with (
        open(os.devnull, "w", encoding="utf-8") as sink,
        redirect_stdout(sink),
        redirect_stderr(sink),
    ):
        yield


def evaluation_list_result(
    root: Path,
    target: str,
    runner: EvaluationRunner,
) -> EvaluationListResult:
    """Return deterministic discovery without any case input payloads."""
    entries = tuple(
        EvaluationEntryResult(
            name=item.name,
            description=item.description,
            kind=item.kind,
            case_schema=item.case_schema,
            cases=tuple(case.name for case in item.cases),
            metrics=tuple(
                EvaluationMetricDeclarationResult(
                    name=metric.name,
                    description=metric.description,
                    threshold=metric.threshold,
                )
                for metric in item.metrics
            ),
            timeout_seconds=item.timeout,
            max_tokens=item.max_tokens,
            max_cost_usd=item.max_cost_usd,
        )
        for item in sorted(
            runner.evaluations,
            key=lambda declared: declared.name,
        )
    )
    return EvaluationListResult(
        root=str(root.resolve()),
        target=target,
        evaluations=entries,
    )


def evaluation_diff_result(
    root: Path,
    *,
    evaluations: str,
    snapshot: Path,
    ref: str | None,
    allow_missing_baseline: bool = False,
) -> EvaluationDiffResult:
    """Generate the evaluation manifest and compare it with a baseline."""
    if allow_missing_baseline and ref is None:
        raise OperationError("allow_missing_baseline requires a Git ref")
    resolved_root = root.resolve()
    with discard_evaluation_output():
        runner = load_evaluation_runner(resolved_root, evaluations)
        current = evaluation_manifest(runner.evaluations)
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
            missing_text=(
                _EMPTY_EVALUATION_MANIFEST if allow_missing_baseline else None
            ),
        )
        baseline_text = baseline.text
        baseline_label = baseline.label
        baseline_present = baseline.present
    return compare_evaluation_baseline(
        resolved_root,
        baseline_text=baseline_text,
        baseline_label=baseline_label,
        current=current,
        baseline_present=baseline_present,
    )


def compare_evaluation_baseline(
    root: Path,
    *,
    baseline_text: str,
    baseline_label: str,
    current: EvaluationManifest,
    baseline_present: bool = True,
) -> EvaluationDiffResult:
    """Compare a generated evaluation manifest with serialized JSON."""
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
        report = analyze_evaluation_compatibility(baseline, current)
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
                    location="evaluation manifest baseline",
                    message=(
                        "historical baseline absent; explicit first-adoption "
                        "override used"
                    ),
                ),
            )
        )
    return EvaluationDiffResult(
        root=str(root.resolve()),
        baseline=baseline_label,
        report=report,
    )


async def evaluation_run_result(
    root: Path,
    target: str,
    runner: EvaluationRunner,
    *,
    name: str | None,
    concurrency: int | None = None,
    timeout: float | None = None,
) -> EvaluationRunResult:
    """Run evaluations and convert all expected failures into safe data."""
    started = perf_counter()
    try:
        report = await runner.run(
            name,
            concurrency=concurrency,
            timeout=timeout,
        )
    except asyncio.CancelledError:
        raise
    except EvaluationNotFoundError:
        return EvaluationRunResult(
            root=str(root.resolve()),
            target=target,
            name=name,
            ok=False,
            duration_seconds=perf_counter() - started,
            evaluations=(),
            error=EvaluationRunErrorResult(
                kind="unknown_evaluation",
                code="EVALUATION_NOT_FOUND",
                message=f"unknown evaluation {name!r}",
            ),
        )
    except Exception:
        logger.exception("Evaluation run failed unexpectedly")
        return EvaluationRunResult(
            root=str(root.resolve()),
            target=target,
            name=name,
            ok=False,
            duration_seconds=perf_counter() - started,
            evaluations=(),
            error=EvaluationRunErrorResult(
                kind="failed",
                code="EVALUATION_RUN_FAILED",
                message="evaluation run failed unexpectedly; inspect application logs",
            ),
        )

    outcomes = tuple(
        EvaluationOutcomeResult(
            name=item.name,
            description=item.description,
            kind=item.kind,
            ok=item.ok,
            duration_seconds=item.duration_seconds,
            cases=tuple(
                EvaluationCaseResult(
                    name=case.name,
                    status=case.status,
                    duration_seconds=case.duration_seconds,
                    scores=case.scores,
                    tokens=case.tokens,
                    cost_usd=case.cost_usd,
                    failure_code=case.failure_code,
                )
                for case in item.cases
            ),
            metrics=tuple(
                EvaluationMetricResult(
                    name=metric.name,
                    average=metric.average,
                    threshold=metric.threshold,
                    passed=metric.passed,
                    samples=metric.samples,
                )
                for metric in item.metrics
            ),
            budget=EvaluationBudgetResult(
                max_tokens=item.budget.max_tokens,
                consumed_tokens=item.budget.consumed_tokens,
                max_cost_usd=item.budget.max_cost_usd,
                consumed_cost_usd=item.budget.consumed_cost_usd,
                status=item.budget.status,
            ),
        )
        for item in report.evaluations
    )
    return EvaluationRunResult(
        root=str(root.resolve()),
        target=target,
        name=name,
        ok=report.ok,
        duration_seconds=report.duration_seconds,
        evaluations=outcomes,
    )
