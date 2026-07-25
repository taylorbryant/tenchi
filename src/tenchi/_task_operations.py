"""Renderer-independent operational task loading, discovery, and execution."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from pathlib import Path
from time import perf_counter
from typing import cast

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from ._cli_results import (
    TaskEntryResult,
    TaskListResult,
    TaskRunErrorResult,
    TaskRunResult,
)
from ._openapi_operations import OperationError
from .errors import AppError
from .tasks import (
    TaskBindingError,
    TaskNotFoundError,
    TaskResultError,
    TaskRunner,
    _task_input_schema,  # pyright: ignore[reportPrivateUsage]
    _task_json_result,  # pyright: ignore[reportPrivateUsage]
    _task_output_schema,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger("tenchi.tasks")
_NO_INPUT = object()


def load_task_runner(root: Path, target: str) -> TaskRunner:
    """Import *target* from *root* and return its task runner."""
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
        raise OperationError(f"could not import {module_name!r}: {exc}") from exc
    if not hasattr(module, attribute):
        raise OperationError(f"module {module_name!r} has no attribute {attribute!r}")
    runner = getattr(module, attribute)
    if not isinstance(runner, TaskRunner):
        raise OperationError(
            f"{target!r} is not a tenchi TaskRunner (got {type(runner).__name__})"
        )
    return runner


def task_list_result(root: Path, target: str, runner: TaskRunner) -> TaskListResult:
    """Return deterministic task discovery with validation schemas."""
    entries = tuple(
        TaskEntryResult(
            name=item.name,
            description=item.description,
            input_required=item.request_required,
            input_schema=cast(dict[str, object] | None, _task_input_schema(item)),
            output_schema=cast(dict[str, object], _task_output_schema(item)),
        )
        for item in sorted(runner.tasks, key=lambda declared: declared.name)
    )
    return TaskListResult(
        root=str(root.resolve()),
        target=target,
        tasks=entries,
    )


async def task_run_result(
    root: Path,
    target: str,
    runner: TaskRunner,
    *,
    name: str,
    input_json: bytes | str | None,
    input: object = _NO_INPUT,
) -> TaskRunResult:
    """Invoke one task and capture expected boundary failures as data."""
    started = perf_counter()
    try:
        output = (
            await runner.run(name, input_json=input_json)
            if input is _NO_INPUT
            else await runner.run(name, input=input)
        )
        declared = next(item for item in runner.tasks if item.name == name)
        rendered = _task_json_result(declared, output)
        return TaskRunResult(
            root=str(root.resolve()),
            target=target,
            name=name,
            ok=True,
            duration_seconds=perf_counter() - started,
            output=rendered,
        )
    except asyncio.CancelledError:
        raise
    except TaskNotFoundError as exc:
        error = TaskRunErrorResult(
            kind="unknown_task",
            code="TASK_NOT_FOUND",
            message=str(exc),
        )
    except (ValidationError, TaskBindingError) as exc:
        details: object | None = None
        if isinstance(exc, ValidationError):
            details = exc.errors(
                include_url=False,
                include_input=False,
                include_context=False,
            )
        error = TaskRunErrorResult(
            kind="invalid_input",
            code="TASK_INPUT_INVALID",
            message=(
                f"input for task {name!r} is invalid"
                if isinstance(exc, ValidationError)
                else str(exc)
            ),
            details=details,
        )
    except AppError as exc:
        error = TaskRunErrorResult(
            kind="application_error",
            code=exc.code,
            message=exc.message,
            details=(
                None
                if exc.details is None
                else to_jsonable_python(exc.details, fallback=str)
            ),
        )
    except TaskResultError as exc:
        error = TaskRunErrorResult(
            kind="invalid_result",
            code="TASK_RESULT_INVALID",
            message=str(exc),
        )
    except Exception:
        logger.exception("Operational task %r failed unexpectedly", name)
        error = TaskRunErrorResult(
            kind="failed",
            code="TASK_FAILED",
            message=f"task {name!r} failed unexpectedly; inspect application logs",
        )
    return TaskRunResult(
        root=str(root.resolve()),
        target=target,
        name=name,
        ok=False,
        duration_seconds=perf_counter() - started,
        error=error,
    )
