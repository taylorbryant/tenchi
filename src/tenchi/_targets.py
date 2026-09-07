"""Default composition targets and the optional modules behind them.

An application must compose routes, a context, and an ASGI application. The
modules that compose background jobs, operational tasks, application tools,
and evaluations are optional: when the convention default target is requested
and its module does not exist, ``map``, ``check``, and ``verify`` treat that
boundary as not configured instead of failing to import it. An explicitly
overridden target is never optional, so a typo still fails loudly. The
preflight module is optional for doctor only; ``tenchi preflight`` requires it.

This module has no framework imports so the scaffold and generators can share
the same definition of "which optional modules exist" as the commands.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

DEFAULT_ROUTES_TARGET = "app.server.routes:routes"
DEFAULT_API_ROUTES_TARGET = "app.server.routes:api_routes"
DEFAULT_APP_TARGET = "app.server.asgi:app"
DEFAULT_PREFLIGHT_TARGET = "app.server.preflight:checks"
DEFAULT_EVALUATIONS_TARGET = "app.server.evaluations:runner"
DEFAULT_TASKS_TARGET = "app.server.tasks:runner"
DEFAULT_JOBS_TARGET = "app.server.jobs:jobs"
DEFAULT_TOOLS_TARGET = "app.server.tools:tools"

# Optional capabilities keyed by the feature file and server module that
# carry them, in the order generators emit them.
OPTIONAL_CAPABILITY_TARGETS: Mapping[str, str] = {
    "tasks": DEFAULT_TASKS_TARGET,
    "jobs": DEFAULT_JOBS_TARGET,
    "tools": DEFAULT_TOOLS_TARGET,
    "evaluations": DEFAULT_EVALUATIONS_TARGET,
}

# ``key -> (requested target, convention default)``.
type OptionalTargets[KeyT] = Mapping[KeyT, tuple[str, str]]


def target_module_paths(target: str) -> tuple[Path, Path]:
    """Return the project-relative files that could define *target*'s module.

    A module may be a single file or a package, so both spellings count.
    """
    module_name = target.partition(":")[0]
    relative = Path(*module_name.split("."))
    return (relative.with_suffix(".py"), relative / "__init__.py")


def present_module_path(root: Path, target: str) -> Path | None:
    """Return the project-relative file that defines *target*, if it exists."""
    resolved_root = root.resolve()
    for path in target_module_paths(target):
        if (resolved_root / path).is_file():
            return path
    return None


def optional_target_absent(root: Path, target: str, default: str) -> bool:
    """True when *target* is the convention default and its module is absent."""
    if target != default:
        return False
    return present_module_path(root, target) is None


__all__ = [
    "DEFAULT_API_ROUTES_TARGET",
    "DEFAULT_APP_TARGET",
    "DEFAULT_EVALUATIONS_TARGET",
    "DEFAULT_JOBS_TARGET",
    "DEFAULT_PREFLIGHT_TARGET",
    "DEFAULT_ROUTES_TARGET",
    "DEFAULT_TASKS_TARGET",
    "DEFAULT_TOOLS_TARGET",
    "OPTIONAL_CAPABILITY_TARGETS",
    "OptionalTargets",
    "optional_target_absent",
    "present_module_path",
    "target_module_paths",
]
