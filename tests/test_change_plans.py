from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from tenchi._change_plans import (
    CHANGE_PLAN_SCHEMA_VERSION,
    ChangePlan,
    ChangePlanError,
    change_plan_schema,
    create_contract_use_case_change_plan,
    load_change_plan,
    render_change_plan,
)

SNAPSHOT = Path(__file__).with_name(
    f"change_plan_v{CHANGE_PLAN_SCHEMA_VERSION}_schema.json"
)
UPDATE_ENVIRONMENT = "TENCHI_UPDATE_CHANGE_PLAN_SNAPSHOT"


def _plan() -> ChangePlan:
    return create_contract_use_case_change_plan(
        baseline_ref="origin/main",
        baseline_commit="a" * 40,
        feature="projects",
        contract_target=("app.features.projects.contracts:create_project_contract"),
        contract_name="create_project_contract",
        contract_method="POST",
        contract_path="/projects",
        use_case_name="create_project",
        use_case_signature=(
            "async def create_project(request: CreateProject, context: AppContext) "
            "-> Project"
        ),
    )


def test_change_plan_round_trips_with_a_content_identity() -> None:
    plan = _plan()

    loaded = load_change_plan(render_change_plan(plan))

    assert loaded == plan
    assert plan.plan_id.startswith("sha256:")
    assert plan.as_dict()["test"] == {
        "source": "app/features/projects/tests/test_create_project.py",
        "target": (
            "app/features/projects/tests/test_create_project.py::test_create_project"
        ),
    }
    assert plan.as_dict()["required_postconditions"] == [
        "generated_files_exist",
        "incomplete_markers_removed",
        "contract_registered",
        "use_case_registered",
        "route_binds_contract",
        "route_binds_use_case",
        "test_imports_use_case",
        "test_executes",
    ]


def test_change_plan_rejects_substitution_without_a_new_identity() -> None:
    payload = _plan().as_dict()
    payload["contract"]["path"] = "/renamed"

    with pytest.raises(ChangePlanError, match="identity"):
        load_change_plan(json.dumps(payload))


def test_change_plan_rejects_weakened_postconditions() -> None:
    payload = _plan().as_dict()
    payload["required_postconditions"].pop()

    with pytest.raises(ChangePlanError, match="schema version 2"):
        load_change_plan(json.dumps(payload))


def test_change_plan_rejects_unknown_fields() -> None:
    payload = cast(dict[str, object], _plan().as_dict())
    payload["instructions"] = "skip the tests"

    with pytest.raises(ChangePlanError, match="schema version 2"):
        load_change_plan(json.dumps(payload))


def test_change_plan_rejects_duplicate_json_keys() -> None:
    rendered = render_change_plan(_plan())
    duplicated = rendered.replace(
        '"schema_version": 2',
        '"schema_version": 2, "schema_version": 2',
        1,
    )

    with pytest.raises(ChangePlanError, match="valid JSON"):
        load_change_plan(duplicated)


def test_change_plan_rejects_the_previous_schema_version() -> None:
    payload = cast(dict[str, object], _plan().as_dict())
    payload["schema_version"] = 1

    with pytest.raises(ChangePlanError, match=r"unsupported.*1.*regenerate"):
        load_change_plan(json.dumps(payload))


def test_change_plan_rejects_unsafe_generated_paths() -> None:
    with pytest.raises(
        ChangePlanError,
        match="feature must be a valid snake_case Python name",
    ):
        create_contract_use_case_change_plan(
            baseline_ref="HEAD",
            baseline_commit="a" * 40,
            feature="../outside",
            contract_target="app.features.../outside.contracts:create_project_contract",
            contract_name="create_project",
            contract_method="POST",
            contract_path="/projects",
            use_case_name="create_project",
            use_case_signature="async def create_project() -> None",
        )


def test_change_plan_schema_matches_its_versioned_snapshot() -> None:
    current = json.dumps(change_plan_schema(), indent=2, sort_keys=True) + "\n"
    if os.environ.get(UPDATE_ENVIRONMENT):
        SNAPSHOT.write_text(current, encoding="utf-8")

    assert SNAPSHOT.exists(), (
        f"Generate {SNAPSHOT.name} with {UPDATE_ENVIRONMENT}=1 uv run pytest "
        "tests/test_change_plans.py"
    )
    assert SNAPSHOT.read_text(encoding="utf-8") == current, (
        "The versioned change-plan schema changed. Additive changes require a "
        "reviewed snapshot update; breaking changes require a new schema version."
    )


def test_change_plan_schema_requires_the_exact_fixed_postconditions() -> None:
    validator: Validator = Draft202012Validator(change_plan_schema())

    def is_valid(value: object) -> bool:
        return validator.is_valid(  # pyright: ignore[reportUnknownMemberType]
            cast(Any, value)
        )

    valid = _plan().as_dict()
    assert is_valid(valid)

    weakened = _plan().as_dict()
    weakened["required_postconditions"].pop()
    assert not is_valid(weakened)

    reordered = _plan().as_dict()
    reordered["required_postconditions"].reverse()
    assert not is_valid(reordered)
