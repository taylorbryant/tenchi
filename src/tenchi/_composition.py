"""Load optional composition groups and inspect them at a Git baseline.

``_targets`` decides whether an optional module exists; this module does the
work that needs the loaders or Git: importing each present group for the
application map, and asking which optional modules existed at a baseline
commit so verification can tell a first adoption from a renamed snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._evaluation_operations import discard_evaluation_output, load_evaluation_runner
from ._job_operations import load_job_group
from ._openapi_operations import git_paths_present
from ._targets import (
    DEFAULT_EVALUATIONS_TARGET,
    DEFAULT_JOBS_TARGET,
    DEFAULT_TASKS_TARGET,
    DEFAULT_TOOLS_TARGET,
    OptionalTargets,
    optional_target_absent,
    target_module_paths,
)
from ._task_operations import load_task_runner
from ._tool_operations import load_tool_group
from .evaluations import EvaluationGroup
from .jobs import JobGroup
from .tasks import TaskGroup
from .tools import ToolGroup


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
    "OptionalGroups",
    "load_optional_groups",
    "optional_targets_absent_at_ref",
]
