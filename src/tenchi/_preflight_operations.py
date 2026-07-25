"""Renderer-independent environment preflight loading and execution."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path

from ._cli_results import (
    PreflightCheckResult,
    PreflightResult,
)
from ._openapi_operations import OperationError
from .preflight import PreflightGroup, run_preflight


def load_preflight_group(root: Path, target: str) -> PreflightGroup:
    """Import *target* from *root* and return its preflight group."""
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
        group = getattr(module, attribute)
    except AttributeError:
        raise OperationError(
            f"module {module_name!r} has no attribute {attribute!r}"
        ) from None
    except Exception as exc:
        raise OperationError(
            f"could not read {target!r} ({type(exc).__name__})"
        ) from exc
    if not isinstance(group, PreflightGroup):
        raise OperationError(
            f"{target!r} is not a tenchi PreflightGroup (got {type(group).__name__})"
        )
    return group


@contextmanager
def discard_preflight_output() -> Generator[None, None, None]:
    """Discard direct process output while loading or running preflight code."""
    with (
        open(os.devnull, "w", encoding="utf-8") as sink,
        redirect_stdout(sink),
        redirect_stderr(sink),
    ):
        yield


async def preflight_result(
    root: Path,
    target: str,
    group: PreflightGroup,
    *,
    timeout: float | None = None,
) -> PreflightResult:
    """Run one application's preflight group and return a versioned result."""
    report = await run_preflight(group, timeout=timeout)
    return PreflightResult(
        root=str(root.resolve()),
        target=target,
        ok=report.ok,
        duration_seconds=report.duration_seconds,
        checks=tuple(
            PreflightCheckResult(
                name=outcome.name,
                description=outcome.description,
                status=outcome.status,
                duration_seconds=outcome.duration_seconds,
                failure_code=outcome.failure_code,
            )
            for outcome in report.outcomes
        ),
    )
