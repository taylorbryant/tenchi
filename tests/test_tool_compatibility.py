"""Directional compatibility checks for application-tool manifests."""

from copy import deepcopy
from typing import Any

import pytest

from tenchi.compatibility import (
    analyze_tool_compatibility,
    render_tool_compatibility_report,
)


def manifest(*tools: dict[str, Any], schema_version: int = 1) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "tools": list(tools),
    }


def tool_entry(
    name: str = "projects.search",
    *,
    input_schema: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    errors: list[dict[str, str]] | None = None,
    annotations: dict[str, bool] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Run {name}.",
        "input_schema": input_schema
        or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": output_schema
        or {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
            "additionalProperties": False,
        },
        "errors": errors or [],
        "annotations": annotations
        or {
            "read_only": True,
            "destructive": False,
            "idempotent": True,
            "open_world": False,
        },
    }


def test_identical_tool_manifests_are_compatible() -> None:
    baseline = manifest(tool_entry())

    report = analyze_tool_compatibility(baseline, deepcopy(baseline))

    assert report.compatible is True
    assert report.status == "compatible"
    assert report.changes == ()


def test_tool_addition_is_additive_and_removal_is_breaking() -> None:
    baseline = manifest(tool_entry())
    current = manifest(tool_entry(), tool_entry("projects.archive"))

    added = analyze_tool_compatibility(baseline, current)
    removed = analyze_tool_compatibility(current, baseline)

    assert [(change.severity, change.message) for change in added.changes] == [
        ("additive", "tool added")
    ]
    assert [(change.severity, change.message) for change in removed.changes] == [
        ("breaking", "tool removed")
    ]


def test_input_narrowing_and_output_widening_are_breaking() -> None:
    baseline = manifest(tool_entry())
    current = deepcopy(baseline)
    current_tool = current["tools"][0]
    current_tool["input_schema"]["required"] = ["query"]
    current_tool["output_schema"]["properties"]["debug"] = {"type": "string"}

    report = analyze_tool_compatibility(baseline, current)

    assert report.compatible is False
    assert {
        (change.severity, change.location, change.message) for change in report.changes
    } == {
        (
            "breaking",
            "tool 'projects.search' input property 'query'",
            "property became required",
        ),
        (
            "breaking",
            "tool 'projects.search' output property 'debug'",
            "property added",
        ),
    }


def test_input_widening_and_output_narrowing_are_additive() -> None:
    current = manifest(tool_entry())
    baseline = deepcopy(current)
    baseline_tool = baseline["tools"][0]
    baseline_tool["input_schema"]["required"] = ["query"]
    current["tools"][0]["output_schema"]["properties"]["count"]["minimum"] = 0

    report = analyze_tool_compatibility(baseline, current)

    assert report.compatible is True
    assert [change.severity for change in report.changes] == [
        "additive",
        "additive",
    ]


def test_nested_referenced_schema_changes_are_compared_directionally() -> None:
    baseline = manifest(
        tool_entry(
            input_schema={
                "$defs": {
                    "Filter": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    }
                },
                "$ref": "#/$defs/Filter",
            }
        )
    )
    current = deepcopy(baseline)
    current["tools"][0]["input_schema"]["$defs"]["Filter"]["required"] = ["limit"]

    report = analyze_tool_compatibility(baseline, current)

    assert any(
        change.severity == "breaking" and change.location.endswith("property 'limit'")
        for change in report.changes
    )


def test_error_and_description_changes_have_explicit_semantics() -> None:
    baseline = manifest(
        tool_entry(
            errors=[
                {"code": "NOT_FOUND", "message": "Not found"},
                {"code": "OLD", "message": "Old failure"},
            ]
        )
    )
    current = deepcopy(baseline)
    current_tool = current["tools"][0]
    current_tool["description"] = "Search visible projects."
    current_tool["errors"] = [
        {"code": "NOT_FOUND", "message": "Project not found"},
        {"code": "NEW", "message": "New failure"},
    ]

    report = analyze_tool_compatibility(baseline, current)

    assert {(change.severity, change.message) for change in report.changes} == {
        ("metadata", "description changed"),
        ("metadata", "error message changed"),
        ("breaking", "application error added"),
        ("additive", "application error removed"),
    }


def test_changed_unknown_error_fields_require_review() -> None:
    baseline = manifest(
        tool_entry(errors=[{"code": "NOT_FOUND", "message": "Not found"}])
    )
    current = deepcopy(baseline)
    baseline["tools"][0]["errors"][0]["retryable"] = False
    current["tools"][0]["errors"][0]["retryable"] = True

    report = analyze_tool_compatibility(baseline, current)

    assert report.status == "review required"
    assert [(change.severity, change.message) for change in report.changes] == [
        ("unknown", "unsupported error fields changed")
    ]


@pytest.mark.parametrize(
    ("annotation", "safe", "unsafe"),
    [
        ("read_only", True, False),
        ("destructive", False, True),
        ("idempotent", True, False),
        ("open_world", False, True),
    ],
)
def test_safety_annotations_fail_when_the_tool_becomes_less_safe(
    annotation: str,
    safe: bool,
    unsafe: bool,
) -> None:
    baseline = manifest(tool_entry())
    current = deepcopy(baseline)
    if annotation in {"destructive", "idempotent"}:
        baseline["tools"][0]["annotations"]["read_only"] = False
        current["tools"][0]["annotations"]["read_only"] = False
    baseline["tools"][0]["annotations"][annotation] = safe
    current["tools"][0]["annotations"][annotation] = unsafe

    less_safe = analyze_tool_compatibility(baseline, current)
    safer = analyze_tool_compatibility(current, baseline)

    assert [(change.severity, change.location) for change in less_safe.changes] == [
        ("breaking", f"tool 'projects.search' annotation {annotation!r}")
    ]
    assert [change.severity for change in safer.changes] == ["additive"]


def test_manifest_version_change_requires_review() -> None:
    baseline = manifest(tool_entry(), schema_version=1)
    current = manifest(tool_entry(), schema_version=2)

    report = analyze_tool_compatibility(baseline, current)

    assert report.status == "review required"
    assert report.compatible is False
    assert report.changes[0].message == "manifest schema version changed"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "must be a tool manifest object"),
        ({"schema_version": 1}, "must contain a tools array"),
        (
            {"schema_version": 1, "tools": [{"name": "broken"}]},
            "must contain a non-empty description",
        ),
        (
            manifest(tool_entry(), tool_entry()),
            "repeats tool 'projects.search'",
        ),
        (
            manifest(tool_entry("Projects Search")),
            "must use dotted snake_case",
        ),
    ],
)
def test_invalid_tool_manifests_are_rejected(value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        analyze_tool_compatibility(value, manifest())


def test_invalid_tool_json_schemas_are_rejected() -> None:
    value = manifest(tool_entry())
    value["tools"][0]["input_schema"] = {"type": "not-a-json-schema-type"}

    with pytest.raises(ValueError, match="is not valid JSON Schema"):
        analyze_tool_compatibility(value, value)


def test_tool_compatibility_report_is_readable() -> None:
    report = analyze_tool_compatibility(
        manifest(tool_entry()),
        manifest(tool_entry("projects.archive")),
    )

    rendered = render_tool_compatibility_report(
        report,
        baseline_path="tools.json",
    )

    assert rendered.startswith("Tool compatibility against tools.json: incompatible\n")
    assert "BREAKING" in rendered
    assert "tool 'projects.search': tool removed" in rendered
