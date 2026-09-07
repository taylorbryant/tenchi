from pathlib import Path

import pytest

from tenchi._targets import (
    DEFAULT_TOOLS_TARGET,
    OPTIONAL_CAPABILITY_TARGETS,
    optional_target_absent,
    present_module_path,
    target_module_paths,
)


def _write(root: Path, relative: str, content: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_target_module_paths_cover_modules_and_packages() -> None:
    assert target_module_paths(DEFAULT_TOOLS_TARGET) == (
        Path("app/server/tools.py"),
        Path("app/server/tools/__init__.py"),
    )


@pytest.mark.parametrize(
    "layout", ["app/server/tools.py", "app/server/tools/__init__.py"]
)
def test_a_default_target_is_present_as_a_module_or_a_package(
    tmp_path: Path, layout: str
) -> None:
    assert present_module_path(tmp_path, DEFAULT_TOOLS_TARGET) is None
    assert optional_target_absent(tmp_path, DEFAULT_TOOLS_TARGET, DEFAULT_TOOLS_TARGET)

    _write(tmp_path, layout)

    assert present_module_path(tmp_path, DEFAULT_TOOLS_TARGET) == Path(layout)
    assert not optional_target_absent(
        tmp_path, DEFAULT_TOOLS_TARGET, DEFAULT_TOOLS_TARGET
    )


def test_an_overridden_target_is_never_optional(tmp_path: Path) -> None:
    assert not optional_target_absent(
        tmp_path, "custom.tools:tools", DEFAULT_TOOLS_TARGET
    )


def test_optional_capabilities_map_to_their_default_targets() -> None:
    assert list(OPTIONAL_CAPABILITY_TARGETS) == [
        "tasks",
        "jobs",
        "tools",
        "evaluations",
    ]
    assert all(
        target.startswith(f"app.server.{name}:")
        for name, target in OPTIONAL_CAPABILITY_TARGETS.items()
    )
