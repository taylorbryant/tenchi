"""Versioned structural intent for contract-driven code generation."""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    WithJsonSchema,
    with_config,
)
from typing_extensions import TypedDict

CHANGE_PLAN_SCHEMA_VERSION = 1
_PYTHON_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

type ChangePlanSchemaVersion = Literal[1]
type ChangePlanKind = Literal["contract-use-case"]
type ChangePlanId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
type ChangePlanCommit = Annotated[
    str,
    Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]
type ChangePlanPostcondition = Literal[
    "generated_files_exist",
    "incomplete_markers_removed",
    "contract_registered",
    "use_case_registered",
    "route_binds_contract",
    "route_binds_use_case",
    "test_imports_use_case",
]

REQUIRED_CHANGE_PLAN_POSTCONDITIONS: tuple[ChangePlanPostcondition, ...] = (
    "generated_files_exist",
    "incomplete_markers_removed",
    "contract_registered",
    "use_case_registered",
    "route_binds_contract",
    "route_binds_use_case",
    "test_imports_use_case",
)
type ChangePlanPostconditions = Annotated[
    list[ChangePlanPostcondition],
    Field(min_length=7, max_length=7),
    WithJsonSchema(
        {
            "type": "array",
            "prefixItems": [
                {"const": value} for value in REQUIRED_CHANGE_PLAN_POSTCONDITIONS
            ],
            "minItems": 7,
            "maxItems": 7,
        }
    ),
]


@with_config(ConfigDict(extra="forbid"))
class ChangePlanBaselinePayload(TypedDict):
    ref: str
    commit: ChangePlanCommit


@with_config(ConfigDict(extra="forbid"))
class ChangePlanContractPayload(TypedDict):
    target: str
    name: str
    method: str
    path: str


@with_config(ConfigDict(extra="forbid"))
class ChangePlanUseCasePayload(TypedDict):
    name: str
    signature: str
    source: str
    test: str


@with_config(ConfigDict(extra="forbid"))
class ChangePlanPayload(TypedDict):
    schema_version: ChangePlanSchemaVersion
    plan_id: ChangePlanId
    kind: ChangePlanKind
    baseline: ChangePlanBaselinePayload
    feature: str
    contract: ChangePlanContractPayload
    use_case: ChangePlanUseCasePayload
    required_postconditions: ChangePlanPostconditions


_CHANGE_PLAN_ADAPTER = TypeAdapter(ChangePlanPayload)


class ChangePlanError(RuntimeError):
    """A malformed or internally inconsistent change plan."""


@dataclass(frozen=True, slots=True)
class ChangePlan:
    """One immutable structural intent derived from an HTTP contract."""

    plan_id: str
    baseline_ref: str
    baseline_commit: str
    feature: str
    contract_target: str
    contract_name: str
    contract_method: str
    contract_path: str
    use_case_name: str
    use_case_signature: str
    use_case_source: str
    test_source: str
    schema_version: ChangePlanSchemaVersion = CHANGE_PLAN_SCHEMA_VERSION
    kind: ChangePlanKind = "contract-use-case"

    def as_dict(self) -> ChangePlanPayload:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "kind": self.kind,
            "baseline": {
                "ref": self.baseline_ref,
                "commit": self.baseline_commit,
            },
            "feature": self.feature,
            "contract": {
                "target": self.contract_target,
                "name": self.contract_name,
                "method": self.contract_method,
                "path": self.contract_path,
            },
            "use_case": {
                "name": self.use_case_name,
                "signature": self.use_case_signature,
                "source": self.use_case_source,
                "test": self.test_source,
            },
            "required_postconditions": list(REQUIRED_CHANGE_PLAN_POSTCONDITIONS),
        }


def create_contract_use_case_change_plan(
    *,
    baseline_ref: str,
    baseline_commit: str,
    feature: str,
    contract_target: str,
    contract_name: str,
    contract_method: str,
    contract_path: str,
    use_case_name: str,
    use_case_signature: str,
) -> ChangePlan:
    """Create the canonical plan for one contract-driven use case."""
    use_case_source = f"app/features/{feature}/use_cases/{use_case_name}.py"
    test_source = f"app/features/{feature}/tests/test_{use_case_name}.py"
    values = {
        "schema_version": CHANGE_PLAN_SCHEMA_VERSION,
        "kind": "contract-use-case",
        "baseline": {"ref": baseline_ref, "commit": baseline_commit},
        "feature": feature,
        "contract": {
            "target": contract_target,
            "name": contract_name,
            "method": contract_method,
            "path": contract_path,
        },
        "use_case": {
            "name": use_case_name,
            "signature": use_case_signature,
            "source": use_case_source,
            "test": test_source,
        },
        "required_postconditions": list(REQUIRED_CHANGE_PLAN_POSTCONDITIONS),
    }
    plan_id = _plan_id(values)
    plan = ChangePlan(
        plan_id=plan_id,
        baseline_ref=baseline_ref,
        baseline_commit=baseline_commit,
        feature=feature,
        contract_target=contract_target,
        contract_name=contract_name,
        contract_method=contract_method,
        contract_path=contract_path,
        use_case_name=use_case_name,
        use_case_signature=use_case_signature,
        use_case_source=use_case_source,
        test_source=test_source,
    )
    _validate_semantics(plan)
    _validate_exact_payload(plan.as_dict())
    return plan


def load_change_plan(text: str) -> ChangePlan:
    """Parse one exact plan and reject weakened or non-canonical fields."""
    try:
        raw: object = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeError, ChangePlanError) as exc:
        raise ChangePlanError("change plan is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ChangePlanError("change plan must be a JSON object")
    payload = _validate_exact_payload(cast(dict[str, object], raw))
    baseline = payload["baseline"]
    contract = payload["contract"]
    use_case = payload["use_case"]
    expected = create_contract_use_case_change_plan(
        baseline_ref=baseline["ref"],
        baseline_commit=baseline["commit"],
        feature=payload["feature"],
        contract_target=contract["target"],
        contract_name=contract["name"],
        contract_method=contract["method"],
        contract_path=contract["path"],
        use_case_name=use_case["name"],
        use_case_signature=use_case["signature"],
    )
    if expected.as_dict() != payload:
        raise ChangePlanError(
            "change plan identity or required postconditions do not match its content"
        )
    _validate_semantics(expected)
    return expected


def render_change_plan(plan: ChangePlan) -> str:
    """Render a deterministic plan suitable for committing or exchanging."""
    return json.dumps(plan.as_dict(), indent=2, sort_keys=True) + "\n"


def change_plan_schema() -> dict[str, object]:
    """Return the serialization schema for change-plan version 1."""
    return cast(
        dict[str, object],
        _CHANGE_PLAN_ADAPTER.json_schema(mode="serialization"),
    )


def _validate_exact_payload(value: Mapping[str, object]) -> ChangePlanPayload:
    raw = dict(value)
    try:
        validated = _CHANGE_PLAN_ADAPTER.validate_python(raw, strict=True)
    except ValidationError as exc:
        raise ChangePlanError("change plan does not match schema version 1") from exc
    if validated != raw:
        raise ChangePlanError("change plan contains unsupported fields or values")
    return validated


def _validate_semantics(plan: ChangePlan) -> None:
    for label, value in (
        ("feature", plan.feature),
        ("contract symbol", plan.contract_target.partition(":")[2]),
        ("use-case name", plan.use_case_name),
    ):
        if not _PYTHON_NAME.fullmatch(value) or keyword.iskeyword(value):
            raise ChangePlanError(
                f"change plan {label} must be a valid snake_case Python name"
            )
    expected_target = (
        f"app.features.{plan.feature}.contracts:"
        f"{plan.contract_target.partition(':')[2]}"
    )
    if plan.contract_target != expected_target:
        raise ChangePlanError(
            "change plan contract target does not belong to the selected feature"
        )
    if not plan.baseline_ref:
        raise ChangePlanError("change plan baseline ref must not be empty")
    if plan.contract_method != plan.contract_method.upper():
        raise ChangePlanError("change plan contract method must be uppercase")
    if not plan.contract_path.startswith("/"):
        raise ChangePlanError("change plan contract path must start with '/'")
    for label, value in (
        ("contract name", plan.contract_name),
        ("contract method", plan.contract_method),
        ("contract path", plan.contract_path),
        ("use-case signature", plan.use_case_signature),
    ):
        if not value:
            raise ChangePlanError(f"change plan {label} must not be empty")


def _plan_id(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ChangePlanError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


__all__ = [
    "CHANGE_PLAN_SCHEMA_VERSION",
    "ChangePlan",
    "ChangePlanError",
    "ChangePlanId",
    "ChangePlanPayload",
    "ChangePlanPostcondition",
    "change_plan_schema",
    "create_contract_use_case_change_plan",
    "load_change_plan",
    "render_change_plan",
]
