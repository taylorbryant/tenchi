import sys
import typing
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel

from tenchi._cli_operations import ContractLoadError, make_use_case_result
from tenchi._generation import GenerationConfigurationError, contract_use_case_plan
from tenchi._openapi_operations import isolated_project_imports
from tenchi.contracts import contract
from tenchi.errors import ConfigurationError
from tenchi.pagination import Page
from tenchi.routes import route

read_ready = type("read_ready", (), {"__module__": __name__})


class Project(BaseModel):
    id: str


class MetadataLookalike:
    __pydantic_generic_metadata__: typing.ClassVar[dict[str, object]] = {}


MetadataLookalike.__pydantic_generic_metadata__ = {
    "origin": MetadataLookalike,
    "args": (MetadataLookalike,),
}

NestedResponse = dict[
    str,
    list[tuple[str, int, bool, bytes, float, complex, object, type, memoryview]],
]


def test_contract_loader_restores_sys_path_after_failure(tmp_path: Path) -> None:
    (tmp_path / "app/features/projects").mkdir(parents=True)
    original_path = sys.path.copy()

    with (
        pytest.raises(ContractLoadError),
        isolated_project_imports(
            tmp_path,
            module_names=("app.features.projects.contracts",),
        ),
    ):
        make_use_case_result(
            tmp_path,
            feature="projects",
            name="create_project",
            dry_run=True,
            from_contract="app.features.projects.contracts:declared",
        )

    assert sys.path == original_path


def test_contract_generation_rolls_back_files_and_change_plan_together(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature = tmp_path / "app/features/projects"
    feature.mkdir(parents=True)
    for package in (tmp_path / "app", tmp_path / "app/features", feature):
        (package / "__init__.py").write_text("", encoding="utf-8")
    (feature / "contracts.py").write_text(
        "from tenchi.contracts import contract\n\n"
        "create_project_contract = contract(\n"
        '    method="POST",\n'
        '    path="/projects",\n'
        "    request=str,\n"
        "    response=str,\n"
        ")\n",
        encoding="utf-8",
    )
    original_replace = Path.replace
    calls = 0

    def fail_plan_write(source: Path, target: Path) -> Path:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated plan write failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_plan_write)
    with isolated_project_imports(
        tmp_path,
        module_names=("app.features.projects.contracts",),
    ):
        result = make_use_case_result(
            tmp_path,
            feature="projects",
            name="create_project",
            dry_run=False,
            from_contract=("app.features.projects.contracts:create_project_contract"),
            change_plan_path=".tenchi/changes/create-project.json",
            change_plan_baseline_ref="HEAD",
            change_plan_baseline_commit="a" * 40,
        )

    assert result.ok is False
    assert result.error is not None and "simulated plan write failure" in result.error
    assert not (feature / "use_cases/create_project.py").exists()
    assert not (feature / "tests/test_create_project.py").exists()
    assert not (tmp_path / ".tenchi").exists()


def test_contract_generation_reports_incomplete_change_plan_configuration(
    tmp_path: Path,
) -> None:
    (tmp_path / "app/features/projects").mkdir(parents=True)

    result = make_use_case_result(
        tmp_path,
        feature="projects",
        name="create_project",
        dry_run=True,
        change_plan_baseline_ref="HEAD",
    )

    assert result.ok is False
    assert result.error is not None
    assert "baseline ref and commit must be provided together" in result.error


def test_contract_use_case_plan_preserves_empty_tuple_annotations() -> None:
    declared = contract(method="GET", path="/ready", response=tuple[()])

    plan = contract_use_case_plan(declared, feature="projects", name="read_ready")

    assert ") -> tuple[()]:" in plan.files["use_cases/read_ready.py"]
    compile(plan.files["use_cases/read_ready.py"], "read_ready.py", "exec")


def test_contract_use_case_plan_uses_a_stable_test_target_name() -> None:
    declared = contract(method="POST", path="/projects", response=str)

    plan = contract_use_case_plan(declared, feature="projects", name="create_project")

    test_source = plan.files["tests/test_create_project.py"]
    assert "def test_create_project() -> None:" in test_source
    assert "pytest.fail" in test_source


def test_contract_use_case_plan_distinguishes_null_body_from_no_body() -> None:
    null_body = contract(method="GET", path="/nullable", response=type(None))
    no_body = contract(method="DELETE", path="/items/1", response=None, status=204)

    null_plan = contract_use_case_plan(
        null_body, feature="projects", name="read_nullable"
    )
    no_body_plan = contract_use_case_plan(
        no_body, feature="projects", name="delete_item"
    )

    assert "from types import NoneType" in null_plan.files["use_cases/read_nullable.py"]
    assert ") -> NoneType:" in null_plan.files["use_cases/read_nullable.py"]
    assert ") -> None:" in no_body_plan.files["use_cases/delete_item.py"]


def test_contract_use_case_plan_preserves_legacy_typing_aliases() -> None:
    legacy_list = vars(typing)["List"][str]
    declared = contract(method="GET", path="/names", response=legacy_list)

    plan = contract_use_case_plan(declared, feature="projects", name="list_names")

    source = plan.files["use_cases/list_names.py"]
    assert "from typing import List" in source
    assert ") -> List[str]:" in source
    compile(source, "list_names.py", "exec")


def test_contract_use_case_plan_reconstructs_pydantic_generics_exactly() -> None:
    declared = contract(method="GET", path="/projects", response=Page[Project])

    plan = contract_use_case_plan(declared, feature="projects", name="list_projects")

    source = plan.files["use_cases/list_projects.py"]
    assert "from tenchi.pagination import Page" in source
    assert "from test_generation import Project" in source
    assert ") -> Page[Project]:" in source
    namespace: dict[str, object] = {}
    exec(compile(source, "list_projects.py", "exec"), namespace)
    route(declared, namespace["list_projects"])  # type: ignore[arg-type]


def test_contract_use_case_plan_ignores_pydantic_metadata_on_plain_classes() -> None:
    declared = contract(method="GET", path="/lookalike", response=MetadataLookalike)

    plan = contract_use_case_plan(declared, feature="projects", name="read_lookalike")

    assert ") -> MetadataLookalike:" in plan.files["use_cases/read_lookalike.py"]


def test_contract_use_case_plan_rejects_overlong_inline_annotations() -> None:
    declared = contract(method="GET", path="/nested", response=NestedResponse)

    with pytest.raises(ConfigurationError, match="named type alias"):
        contract_use_case_plan(declared, feature="projects", name="read_nested")


def test_contract_use_case_plan_uses_named_alias_for_overlong_annotation() -> None:
    declared = contract(method="GET", path="/nested", response=NestedResponse)

    plan = contract_use_case_plan(
        declared,
        feature="projects",
        name="read_nested",
        named_annotations={
            id(NestedResponse): (NestedResponse, __name__, "NestedResponse")
        },
    )

    assert ") -> NestedResponse:" in plan.files["use_cases/read_nested.py"]


def test_contract_use_case_plan_rejects_unsafe_named_alias_imports() -> None:
    declared = contract(method="GET", path="/projects", response=Project)

    with pytest.raises(GenerationConfigurationError, match="cannot render import"):
        contract_use_case_plan(
            declared,
            feature="projects",
            name="read_project",
            named_annotations={id(Project): (Project, "unsafe-module", "Project")},
        )


def test_contract_use_case_plan_rejects_overlong_generated_names() -> None:
    declared = contract(method="GET", path="/items", response=str)

    with pytest.raises(ConfigurationError, match="shorter names"):
        contract_use_case_plan(
            declared,
            feature="projects",
            name=f"read_{'item_' * 20}",
        )


def test_contract_use_case_plan_rejects_unsafe_literal_enum_members() -> None:
    unusual = Enum("Unusual", {"not-valid": "value"}, module=__name__)
    declared = contract(
        method="GET",
        path="/unusual",
        response=typing.Literal[unusual["not-valid"]],  # pyright: ignore[reportArgumentType]
    )

    with pytest.raises(ConfigurationError, match="cannot be rendered"):
        contract_use_case_plan(declared, feature="projects", name="read_unusual")


def test_contract_use_case_plan_avoids_shadowing_types_with_the_function() -> None:
    declared = contract(method="GET", path="/ready", response=read_ready)

    plan = contract_use_case_plan(declared, feature="projects", name="read_ready")

    source = plan.files["use_cases/read_ready.py"]
    assert "from test_generation import read_ready as _read_ready_2" in source
    assert ") -> _read_ready_2:" in source


def test_contract_use_case_plan_avoids_shadowing_builtins_with_the_function() -> None:
    declared = contract(method="GET", path="/items", response=list[str])

    plan = contract_use_case_plan(declared, feature="projects", name="list")

    source = plan.files["use_cases/list.py"]
    assert "from builtins import list as _list_2" in source
    assert ") -> _list_2[str]:" in source
