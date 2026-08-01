"""Renderer-independent evaluation discovery and execution."""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from time import perf_counter

from ._cli_results import (
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
from ._openapi_operations import OperationError
from .evaluations import (
    EvaluationNotFoundError,
    EvaluationRunner,
)

logger = logging.getLogger("tenchi.evaluations")


def load_evaluation_runner(root: Path, target: str) -> EvaluationRunner:
    """Import *target* from *root* and return its evaluation runner."""
    resolved_root = root.resolve()
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise OperationError(f"expected module:attribute, got {target!r}")

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
            f"{target!r} is not a tenchi EvaluationRunner (got {type(runner).__name__})"
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
