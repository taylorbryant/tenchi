"""Evaluation policy compatibility fails closed when a gate is weakened."""

from copy import deepcopy
from typing import Any

import pytest

from tenchi.compatibility import (
    CompatibilityReport,
    analyze_evaluation_compatibility,
    render_evaluation_compatibility_report,
)


def manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "evaluations": [
            {
                "name": "answers.quality",
                "description": "Protect answer quality.",
                "kind": "model",
                "case_schema": {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
                "cases": ["answers.quality.simple"],
                "metrics": [
                    {
                        "name": "quality",
                        "description": "Answer quality.",
                        "threshold": 0.8,
                    }
                ],
                "timeout_seconds": 30.0,
                "max_tokens": 100,
                "max_cost_usd": 1.0,
            }
        ],
    }


def severities(report: CompatibilityReport) -> list[str]:
    return [change.severity for change in report.changes]


def messages(report: CompatibilityReport) -> list[str]:
    return [change.message for change in report.changes]


def test_identical_evaluation_policies_are_compatible() -> None:
    baseline = manifest()

    report = analyze_evaluation_compatibility(baseline, deepcopy(baseline))

    assert report.compatible
    assert report.changes == ()


def test_added_and_removed_evaluations_cases_and_metrics_are_directional() -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    current["evaluations"].append(
        {
            **deepcopy(current["evaluations"][0]),
            "name": "answers.safety",
        }
    )
    current["evaluations"][0]["cases"].append("answers.quality.hard")
    current["evaluations"][0]["metrics"].append(
        {"name": "safety", "description": None, "threshold": 1.0}
    )

    additive = analyze_evaluation_compatibility(baseline, current)
    breaking = analyze_evaluation_compatibility(current, baseline)

    assert severities(additive) == ["additive", "additive", "additive"]
    assert severities(breaking) == ["breaking", "breaking", "breaking"]


@pytest.mark.parametrize(
    ("field", "before", "after", "expected"),
    [
        pytest.param("threshold", 0.8, 0.9, "additive", id="threshold-raised"),
        pytest.param("threshold", 0.8, 0.7, "breaking", id="threshold-lowered"),
        pytest.param("timeout_seconds", 30.0, 20.0, "additive", id="timeout-lowered"),
        pytest.param("timeout_seconds", 30.0, 40.0, "breaking", id="timeout-raised"),
        pytest.param("max_tokens", 100, 90, "additive", id="tokens-lowered"),
        pytest.param("max_tokens", 100, 110, "breaking", id="tokens-raised"),
        pytest.param("max_cost_usd", 1.0, 0.5, "additive", id="cost-lowered"),
        pytest.param("max_cost_usd", 1.0, 2.0, "breaking", id="cost-raised"),
    ],
)
def test_policy_limits_are_directional(
    field: str,
    before: float,
    after: float,
    expected: str,
) -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    target = (
        current["evaluations"][0]["metrics"][0]
        if field == "threshold"
        else current["evaluations"][0]
    )
    target[field] = after
    baseline_target = (
        baseline["evaluations"][0]["metrics"][0]
        if field == "threshold"
        else baseline["evaluations"][0]
    )
    baseline_target[field] = before

    report = analyze_evaluation_compatibility(baseline, current)

    assert severities(report) == [expected]


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        pytest.param(None, 100, "additive", id="budget-added"),
        pytest.param(100, None, "breaking", id="budget-removed"),
    ],
)
def test_optional_budgets_are_directional(
    before: int | None,
    after: int | None,
    expected: str,
) -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    baseline["evaluations"][0]["max_tokens"] = before
    current["evaluations"][0]["max_tokens"] = after

    report = analyze_evaluation_compatibility(baseline, current)

    assert severities(report) == [expected]


def test_kind_and_case_schema_changes_fail_closed() -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    current["evaluations"][0]["kind"] = "deterministic"
    current["evaluations"][0]["case_schema"]["properties"]["prompt"] = {
        "type": "integer"
    }

    report = analyze_evaluation_compatibility(baseline, current)

    assert severities(report) == ["additive", "unknown"]
    assert report.status == "review required"
    assert "case schema changed" in messages(report)


def test_reordered_cases_require_review() -> None:
    baseline = manifest()
    baseline["evaluations"][0]["cases"].append("answers.quality.hard")
    current = deepcopy(baseline)
    current["evaluations"][0]["cases"].reverse()

    report = analyze_evaluation_compatibility(baseline, current)

    assert severities(report) == ["unknown"]
    assert messages(report) == ["case execution order changed"]
    assert report.status == "review required"


def test_switching_a_deterministic_gate_to_model_is_breaking() -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    baseline["evaluations"][0]["kind"] = "deterministic"

    report = analyze_evaluation_compatibility(baseline, current)

    assert severities(report) == ["breaking"]


def test_descriptions_are_metadata_and_unknown_fields_require_review() -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    current["evaluations"][0]["description"] = "Updated copy."
    current["evaluations"][0]["future_policy"] = True

    report = analyze_evaluation_compatibility(baseline, current)

    assert severities(report) == ["metadata", "unknown"]
    assert report.status == "review required"


def test_human_report_explains_the_policy_result() -> None:
    baseline = manifest()
    current = deepcopy(baseline)
    current["evaluations"][0]["metrics"][0]["threshold"] = 0.5
    report = analyze_evaluation_compatibility(baseline, current)

    rendered = render_evaluation_compatibility_report(
        report,
        baseline_path="evaluations.json",
    )

    assert "Evaluation compatibility against evaluations.json: incompatible" in rendered
    assert "BREAKING" in rendered
    assert "threshold decreased from 0.8 to 0.5" in rendered


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({}, id="empty"),
        pytest.param([], id="array"),
        pytest.param({"schema_version": 1}, id="missing-evaluations"),
        pytest.param(
            {"schema_version": 1, "evaluations": [{"name": "Bad"}]},
            id="invalid-evaluation",
        ),
        pytest.param(
            {
                **manifest(),
                "evaluations": [
                    {
                        **manifest()["evaluations"][0],
                        "metrics": [
                            {
                                "name": "quality",
                                "description": None,
                                "threshold": float("nan"),
                            }
                        ],
                    }
                ],
            },
            id="nonfinite-threshold",
        ),
        pytest.param(
            {
                **manifest(),
                "evaluations": [
                    {
                        **manifest()["evaluations"][0],
                        "metrics": [
                            {
                                "name": "quality",
                                "description": None,
                                "threshold": 10**4000,
                            }
                        ],
                    }
                ],
            },
            id="unrepresentable-threshold",
        ),
        pytest.param(
            {
                **manifest(),
                "evaluations": [
                    {
                        **manifest()["evaluations"][0],
                        "timeout_seconds": 10**4000,
                    }
                ],
            },
            id="unrepresentable-timeout",
        ),
        pytest.param(
            {
                **manifest(),
                "evaluations": [
                    {
                        **manifest()["evaluations"][0],
                        "max_cost_usd": 10**4000,
                    }
                ],
            },
            id="unrepresentable-cost-budget",
        ),
    ],
)
def test_invalid_evaluation_manifests_are_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        analyze_evaluation_compatibility(value, manifest())
