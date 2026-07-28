"""Renderer-independent background-job loading for application inspection."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from ._openapi_operations import OperationError
from .jobs import JobGroup


def load_job_group(root: Path, target: str) -> JobGroup:
    """Import *target* from *root* and return its registered job group."""
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
    group = getattr(module, attribute)
    if not isinstance(group, JobGroup):
        raise OperationError(
            f"{target!r} is not a tenchi JobGroup (got {type(group).__name__})"
        )
    return group
