"""Public project loaders do not leak application import state."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tenchi._evaluation_operations import load_evaluation_runner
from tenchi._job_operations import load_job_group
from tenchi._openapi_operations import isolated_project_imports, load_route_group
from tenchi._preflight_operations import load_preflight_group
from tenchi._task_operations import load_task_runner
from tenchi._tool_operations import load_tool_group


@pytest.mark.parametrize(
    ("loader", "target", "source"),
    [
        (
            load_route_group,
            "app.server.routes:routes",
            "from tenchi.routes import route_group\nroutes = route_group()\n",
        ),
        (
            load_task_runner,
            "app.server.tasks:runner",
            "from tenchi.tasks import create_task_runner, task_group\n"
            "runner = create_task_runner(tasks=task_group(), context_factory=object)\n",
        ),
        (
            load_job_group,
            "app.server.jobs:jobs",
            "from tenchi.jobs import job_group\njobs = job_group()\n",
        ),
        (
            load_tool_group,
            "app.server.tools:tools",
            "from tenchi.tools import tool_group\ntools = tool_group()\n",
        ),
        (
            load_evaluation_runner,
            "app.server.evaluations:runner",
            "from tenchi.evaluations import (\n"
            "    create_evaluation_runner, evaluation_group,\n"
            ")\n"
            "runner = create_evaluation_runner(\n"
            "    evaluations=evaluation_group(), context_factory=object\n"
            ")\n",
        ),
        (
            load_preflight_group,
            "app.server.preflight:checks",
            "from tenchi.preflight import preflight_group\n"
            "checks = preflight_group()\n",
        ),
    ],
)
def test_public_loader_restores_project_import_state(
    tmp_path: Path,
    loader: Callable[[Path, str], object],
    target: str,
    source: str,
) -> None:
    module_name = target.partition(":")[0]
    module_path = tmp_path.joinpath(*module_name.split(".")).with_suffix(".py")
    module_path.parent.mkdir(parents=True)
    module_path.write_text(source, encoding="utf-8")
    original_path = list(sys.path)
    original_app_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    }

    loaded = loader(tmp_path, target)

    assert loaded is not None
    assert sys.path == original_path
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "app" or name.startswith("app.")
    } == original_app_modules


def test_project_import_isolation_preserves_the_editable_framework() -> None:
    import tenchi.tools as tools_module

    repository_root = Path(__file__).parents[1]
    with isolated_project_imports(
        repository_root,
        module_names=("app.server.tools",),
    ):
        assert sys.modules["tenchi.tools"] is tools_module
