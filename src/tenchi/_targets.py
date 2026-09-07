"""Default composition targets and detection of optional composition modules.

An application must compose routes, a context, and an ASGI application. The
modules that compose background jobs, operational tasks, application tools,
evaluations, and preflight checks are optional: when the convention default
target is requested and its module does not exist, commands treat that
boundary as not configured instead of failing to import it. An explicitly
overridden target is never optional, so a typo still fails loudly.
"""

from __future__ import annotations

from pathlib import Path

from ._openapi_operations import read_git_snapshot

DEFAULT_ROUTES_TARGET = "app.server.routes:routes"
DEFAULT_API_ROUTES_TARGET = "app.server.routes:api_routes"
DEFAULT_APP_TARGET = "app.server.asgi:app"
DEFAULT_PREFLIGHT_TARGET = "app.server.preflight:checks"
DEFAULT_EVALUATIONS_TARGET = "app.server.evaluations:runner"
DEFAULT_TASKS_TARGET = "app.server.tasks:runner"
DEFAULT_JOBS_TARGET = "app.server.jobs:jobs"
DEFAULT_TOOLS_TARGET = "app.server.tools:tools"


def target_module_path(target: str) -> Path:
    """Return the project-relative module file that defines *target*."""
    module_name = target.partition(":")[0]
    return Path(*module_name.split(".")).with_suffix(".py")


def optional_target_absent(root: Path, target: str, default: str) -> bool:
    """True when *target* is the convention default and its module is absent."""
    if target != default:
        return False
    return not (root.resolve() / target_module_path(target)).is_file()


def optional_target_absent_at_ref(
    root: Path,
    target: str,
    default: str,
    *,
    ref: str,
) -> bool:
    """True when the default module for *target* did not exist at *ref*."""
    if target != default:
        return False
    snapshot = read_git_snapshot(
        root,
        ref=ref,
        snapshot=root.resolve() / target_module_path(target),
        missing_text="",
    )
    return not snapshot.present


__all__ = [
    "DEFAULT_API_ROUTES_TARGET",
    "DEFAULT_APP_TARGET",
    "DEFAULT_EVALUATIONS_TARGET",
    "DEFAULT_JOBS_TARGET",
    "DEFAULT_PREFLIGHT_TARGET",
    "DEFAULT_ROUTES_TARGET",
    "DEFAULT_TASKS_TARGET",
    "DEFAULT_TOOLS_TARGET",
    "optional_target_absent",
    "optional_target_absent_at_ref",
    "target_module_path",
]
