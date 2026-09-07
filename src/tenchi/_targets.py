"""Default composition targets and the optional modules behind them.

An application must compose routes, a context, and an ASGI application. The
modules that compose background jobs, operational tasks, application tools,
and evaluations are optional: when the convention default target is requested
and its module does not exist, ``map``, ``check``, and ``verify`` treat that
boundary as not configured instead of failing to import it. An explicitly
overridden target is never optional, so a typo still fails loudly. The
preflight module is optional for doctor only; ``tenchi preflight`` requires it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ._evaluation_operations import discard_evaluation_output, load_evaluation_runner
from ._job_operations import load_job_group
from ._openapi_operations import git_paths_present
from ._task_operations import load_task_runner
from ._tool_operations import load_tool_group
from .evaluations import EvaluationGroup
from .jobs import JobGroup
from .tasks import TaskGroup
from .tools import ToolGroup

DEFAULT_ROUTES_TARGET = "app.server.routes:routes"
DEFAULT_API_ROUTES_TARGET = "app.server.routes:api_routes"
DEFAULT_APP_TARGET = "app.server.asgi:app"
DEFAULT_PREFLIGHT_TARGET = "app.server.preflight:checks"
DEFAULT_EVALUATIONS_TARGET = "app.server.evaluations:runner"
DEFAULT_TASKS_TARGET = "app.server.tasks:runner"
DEFAULT_JOBS_TARGET = "app.server.jobs:jobs"
DEFAULT_TOOLS_TARGET = "app.server.tools:tools"

# ``key -> (requested target, convention default)``.
type OptionalTargets[KeyT] = Mapping[KeyT, tuple[str, str]]


def target_module_paths(target: str) -> tuple[Path, Path]:
    """Return the project-relative files that could define *target*'s module.

    A module may be a single file or a package, so both spellings count.
    """
    module_name = target.partition(":")[0]
    relative = Path(*module_name.split("."))
    return (relative.with_suffix(".py"), relative / "__init__.py")


def optional_target_absent(root: Path, target: str, default: str) -> bool:
    """True when *target* is the convention default and its module is absent."""
    if target != default:
        return False
    resolved_root = root.resolve()
    return not any(
        (resolved_root / path).is_file() for path in target_module_paths(target)
    )


def optional_targets_absent_at_ref[KeyT](
    root: Path,
    *,
    ref: str,
    targets: OptionalTargets[KeyT],
) -> frozenset[KeyT]:
    """Return the keys whose default module did not exist at *ref*.

    Every candidate path is checked with one Git call.
    """
    candidates: dict[KeyT, tuple[Path, Path]] = {
        key: target_module_paths(target)
        for key, (target, default) in targets.items()
        if target == default
    }
    if not candidates:
        return frozenset()
    present = git_paths_present(
        root,
        ref=ref,
        paths=[path for paths in candidates.values() for path in paths],
    )
    return frozenset(
        key
        for key, paths in candidates.items()
        if not any(path in present for path in paths)
    )


@dataclass(frozen=True, slots=True)
class OptionalGroups:
    """The optional composition groups an application map can include."""

    tasks: TaskGroup | None
    jobs: JobGroup | None
    tools: ToolGroup | None
    evaluations: EvaluationGroup | None


def load_optional_groups(
    root: Path,
    *,
    tasks: str,
    jobs: str,
    tools: str,
    evaluations: str,
) -> OptionalGroups:
    """Load each optional group, or ``None`` when its default module is absent.

    Evaluation modules may print during import; that output is discarded so
    machine-readable results stay clean, matching every other evaluation load.
    """
    loaded_evaluations: EvaluationGroup | None = None
    if not optional_target_absent(root, evaluations, DEFAULT_EVALUATIONS_TARGET):
        with discard_evaluation_output():
            loaded_evaluations = load_evaluation_runner(root, evaluations).evaluations
    return OptionalGroups(
        tasks=(
            None
            if optional_target_absent(root, tasks, DEFAULT_TASKS_TARGET)
            else load_task_runner(root, tasks).tasks
        ),
        jobs=(
            None
            if optional_target_absent(root, jobs, DEFAULT_JOBS_TARGET)
            else load_job_group(root, jobs)
        ),
        tools=(
            None
            if optional_target_absent(root, tools, DEFAULT_TOOLS_TARGET)
            else load_tool_group(root, tools)
        ),
        evaluations=loaded_evaluations,
    )


__all__ = [
    "DEFAULT_API_ROUTES_TARGET",
    "DEFAULT_APP_TARGET",
    "DEFAULT_EVALUATIONS_TARGET",
    "DEFAULT_JOBS_TARGET",
    "DEFAULT_PREFLIGHT_TARGET",
    "DEFAULT_ROUTES_TARGET",
    "DEFAULT_TASKS_TARGET",
    "DEFAULT_TOOLS_TARGET",
    "OptionalGroups",
    "OptionalTargets",
    "load_optional_groups",
    "optional_target_absent",
    "optional_targets_absent_at_ref",
    "target_module_paths",
]
