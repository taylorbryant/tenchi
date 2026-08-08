"""Conservative compatibility classification for Tenchi OpenAPI snapshots."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, Field

from tenchi.compatibility import (
    CompatibilityReport,
    analyze_openapi_compatibility,
    render_compatibility_report,
)
from tenchi.contracts import contract
from tenchi.openapi import openapi_schema
from tenchi.routes import route, route_group


def document() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Example", "version": "1.0.0"},
        "paths": {
            "/items": {
                "post": {
                    "operationId": "create_item",
                    "summary": "Create an item",
                    "parameters": [
                        {
                            "in": "query",
                            "name": "preview",
                            "required": False,
                            "schema": {"type": "boolean"},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {
                                            "type": "string",
                                            "maxLength": 100,
                                        }
                                    },
                                    "required": ["name"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            },
                            "headers": {
                                "Location": {
                                    "required": True,
                                    "schema": {"type": "string"},
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["id", "name"],
                }
            }
        },
    }


def severities(report: CompatibilityReport) -> list[str]:
    return [change.severity for change in report.changes]


def messages(report: CompatibilityReport) -> list[str]:
    return [change.message for change in report.changes]


def add_tenchi_error_response(
    value: dict[str, Any],
    *,
    status: str,
    codes: list[str],
    sources: list[str],
) -> None:
    value["components"]["schemas"]["ErrorResponse"] = {
        "title": "ErrorResponse",
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "details": {},
            "request_id": {"type": "string"},
        },
        "required": ["code", "message"],
    }
    value["paths"]["/items"]["post"]["responses"][status] = {
        "description": "; ".join(codes),
        "content": {
            "application/json": {
                "schema": {
                    "allOf": [
                        {"$ref": "#/components/schemas/ErrorResponse"},
                        {
                            "type": "object",
                            "properties": {"code": {"type": "string", "enum": codes}},
                        },
                    ]
                }
            }
        },
        "headers": {
            "x-tenchi-error-source": {
                "description": "Error source.",
                "required": True,
                "schema": {"type": "string", "enum": sources},
            }
        },
    }


def test_identical_documents_are_compatible() -> None:
    baseline = document()

    report = analyze_openapi_compatibility(baseline, deepcopy(baseline))

    assert report.compatible is True
    assert report.status == "compatible"
    assert report.changes == ()


def test_added_and_removed_operations_are_classified() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/health"] = {
        "get": {"operationId": "health", "responses": {"200": {"description": "OK"}}}
    }

    additive = analyze_openapi_compatibility(baseline, current)
    breaking = analyze_openapi_compatibility(current, baseline)

    assert severities(additive) == ["additive"]
    assert messages(additive) == ["operation added"]
    assert severities(breaking) == ["breaking"]
    assert messages(breaking) == ["operation removed"]


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        pytest.param(False, "additive", id="optional"),
        pytest.param(True, "breaking", id="required"),
    ],
)
def test_added_parameters_respect_requiredness(required: bool, expected: str) -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["parameters"].append(
        {
            "in": "header",
            "name": "x-mode",
            "required": required,
            "schema": {"type": "string"},
        }
    )

    report = analyze_openapi_compatibility(baseline, current)

    assert expected in severities(report)
    assert (
        "required parameter added" if required else "optional parameter added"
    ) in messages(report)


@pytest.mark.parametrize(
    ("case", "expected", "message"),
    [
        pytest.param(
            "required",
            "breaking",
            "required property added",
            id="required-property-added",
        ),
        pytest.param(
            "optional",
            "additive",
            "property added",
            id="optional-property-added",
        ),
        pytest.param(
            "max_length",
            "breaking",
            "maxLength constraint changed",
            id="input-tightened",
        ),
        pytest.param(
            "enum",
            "breaking",
            "enum restriction added",
            id="input-enum-restricted",
        ),
    ],
)
def test_request_schema_changes_are_directional(
    case: str, expected: str, message: str
) -> None:
    baseline = document()
    current = deepcopy(baseline)
    schema = current["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    if case == "required":
        schema["properties"]["nickname"] = {"type": "string"}
        schema["required"].append("nickname")
    elif case == "optional":
        schema["properties"]["nickname"] = {"type": "string"}
    elif case == "max_length":
        schema["properties"]["name"]["maxLength"] = 20
    else:
        schema["properties"]["name"]["enum"] = ["a", "b"]

    report = analyze_openapi_compatibility(baseline, current)

    assert expected in severities(report)
    assert message in messages(report)


def test_response_component_changes_are_resolved_in_output_direction() -> None:
    baseline = document()
    current = deepcopy(baseline)
    del current["components"]["schemas"]["Item"]["properties"]["name"]
    current["components"]["schemas"]["Item"]["required"].remove("name")

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "property removed" in messages(report)
    assert any(
        change.location.endswith("property 'name'")
        for change in report.changes
        if change.severity == "breaking"
    )


def test_array_item_component_changes_are_resolved() -> None:
    baseline = document()
    baseline["paths"]["/items"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Item"},
    }
    current = deepcopy(baseline)
    del current["components"]["schemas"]["Item"]["properties"]["name"]
    current["components"]["schemas"]["Item"]["required"].remove("name")

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "property removed" in messages(report)


def test_any_of_component_changes_keep_input_direction() -> None:
    baseline = document()
    baseline["components"]["schemas"]["State"] = {
        "type": "string",
        "enum": ["active"],
    }
    baseline["paths"]["/items"]["post"]["parameters"][0]["schema"] = {
        "anyOf": [
            {"$ref": "#/components/schemas/State"},
            {"type": "null"},
        ]
    }
    current = deepcopy(baseline)
    current["components"]["schemas"]["State"]["enum"].append("archived")

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is True
    assert severities(report) == ["additive"]
    assert messages(report) == ["enum values changed"]


def test_transitive_component_changes_are_resolved() -> None:
    baseline = document()
    baseline["components"]["schemas"]["Detail"] = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    }
    baseline["components"]["schemas"]["Item"] = {
        "type": "object",
        "properties": {
            "detail": {"$ref": "#/components/schemas/Detail"},
        },
        "required": ["detail"],
    }
    current = deepcopy(baseline)
    del current["components"]["schemas"]["Detail"]["properties"]["label"]
    current["components"]["schemas"]["Detail"]["required"].remove("label")

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "property removed" in messages(report)


def test_component_changes_in_reference_siblings_require_review() -> None:
    baseline = document()
    baseline["components"]["schemas"]["Base"] = {"type": "object"}
    baseline["components"]["schemas"]["Extra"] = {"type": "string"}
    baseline["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] = {
        "$ref": "#/components/schemas/Base",
        "properties": {
            "extra": {"$ref": "#/components/schemas/Extra"},
        },
    }
    current = deepcopy(baseline)
    current["components"]["schemas"]["Extra"] = {"type": "integer"}

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert report.status == "review required"
    assert severities(report) == ["unknown"]
    assert messages(report) == ["schema reference siblings changed"]


def test_referenced_one_of_component_changes_require_review() -> None:
    baseline = document()
    baseline["components"]["schemas"]["Choice"] = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    }
    baseline["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ] = {"oneOf": [{"$ref": "#/components/schemas/Choice"}]}
    current = deepcopy(baseline)
    del current["components"]["schemas"]["Choice"]["properties"]["label"]
    current["components"]["schemas"]["Choice"]["required"].remove("label")

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert report.status == "review required"
    assert severities(report) == ["unknown"]
    assert messages(report) == ["oneOf schema changed"]


@pytest.mark.parametrize(
    ("direction", "reverse", "expected", "message"),
    [
        pytest.param(
            "request", False, "breaking", "property became required", id="input-add"
        ),
        pytest.param(
            "request", True, "additive", "property became optional", id="input-remove"
        ),
        pytest.param(
            "response", False, "additive", "property became required", id="output-add"
        ),
        pytest.param(
            "response", True, "breaking", "property became optional", id="output-remove"
        ),
    ],
)
def test_required_names_without_properties_are_directional(
    direction: str, reverse: bool, expected: str, message: str
) -> None:
    without_required = document()
    with_required = deepcopy(without_required)
    if direction == "request":
        schema = with_required["paths"]["/items"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
    else:
        schema = with_required["components"]["schemas"]["Item"]
    schema["required"].append("token")
    baseline, current = (
        (with_required, without_required)
        if reverse
        else (without_required, with_required)
    )

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is (expected == "additive")
    assert severities(report) == [expected]
    assert messages(report) == [message]
    assert report.changes[0].location.endswith("property 'token'")


def test_response_enum_widening_breaks_old_clients() -> None:
    baseline = document()
    current = deepcopy(baseline)
    baseline["components"]["schemas"]["Item"]["properties"]["name"]["enum"] = ["a"]
    current["components"]["schemas"]["Item"]["properties"]["name"]["enum"] = [
        "a",
        "b",
    ]

    report = analyze_openapi_compatibility(baseline, current)

    assert "breaking" in severities(report)
    assert "enum values changed" in messages(report)


def test_response_property_addition_breaks_strict_clients() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["components"]["schemas"]["Item"]["properties"]["nickname"] = {
        "type": "string"
    }

    report = analyze_openapi_compatibility(baseline, current)

    assert "breaking" in severities(report)
    assert "property added" in messages(report)


@pytest.mark.parametrize(
    ("direction", "reverse", "expected"),
    [
        pytest.param("request", False, "additive", id="input-type-relaxed"),
        pytest.param("request", True, "breaking", id="input-type-restricted"),
        pytest.param("response", False, "breaking", id="output-type-relaxed"),
        pytest.param("response", True, "additive", id="output-type-restricted"),
    ],
)
def test_adding_and_removing_schema_types_is_directional(
    direction: str, reverse: bool, expected: str
) -> None:
    baseline = document()
    current = deepcopy(baseline)
    if direction == "request":
        schema = current["paths"]["/items"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["properties"]["name"]
    else:
        schema = current["components"]["schemas"]["Item"]["properties"]["name"]
    del schema["type"]

    before, after = (current, baseline) if reverse else (baseline, current)
    report = analyze_openapi_compatibility(before, after)

    assert expected in severities(report)
    assert "schema type" in messages(report)[0]


def test_explicit_null_constant_is_not_confused_with_a_missing_keyword() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]["name"]["const"] = None

    report = analyze_openapi_compatibility(baseline, current)

    assert "breaking" in severities(report)
    assert "constant value added" in messages(report)


def test_reordering_set_valued_schema_keywords_has_no_semantic_change() -> None:
    baseline = document()
    current = deepcopy(baseline)
    before_name = baseline["components"]["schemas"]["Item"]["properties"]["name"]
    after_name = current["components"]["schemas"]["Item"]["properties"]["name"]
    before_name["enum"] = ["a", "b"]
    after_name["enum"] = ["b", "a"]

    report = analyze_openapi_compatibility(baseline, current)

    assert report.changes == ()


def test_changed_one_of_requires_review() -> None:
    baseline = document()
    current = deepcopy(baseline)
    baseline_schema = baseline["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    current_schema = current["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    baseline_schema["oneOf"] = [{"type": "integer"}]
    current_schema["oneOf"] = [
        {"type": "integer"},
        {"type": "integer", "minimum": 0},
    ]

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert report.status == "review required"
    assert "oneOf schema changed" in messages(report)


def test_duplicated_one_of_alternative_requires_review() -> None:
    baseline = document()
    current = deepcopy(baseline)
    baseline_schema = baseline["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    current_schema = current["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    alternative = {"type": "integer"}
    baseline_schema["oneOf"] = [alternative]
    current_schema["oneOf"] = [alternative, alternative]

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "oneOf schema changed" in messages(report)


def test_changed_malformed_one_of_fails_closed() -> None:
    baseline = document()
    current = deepcopy(baseline)
    baseline_schema = baseline["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    current_schema = current["paths"]["/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    baseline_schema["oneOf"] = {"type": "integer"}
    current_schema["oneOf"] = {"type": "string"}

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "oneOf schema changed" in messages(report)


def test_response_headers_are_additive_when_added_and_breaking_when_removed() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current_headers = current["paths"]["/items"]["post"]["responses"]["201"]["headers"]
    current_headers["ETag"] = {
        "required": True,
        "schema": {"type": "string"},
    }

    added = analyze_openapi_compatibility(baseline, current)
    removed = analyze_openapi_compatibility(current, baseline)

    assert "response header added" in messages(added)
    assert "additive" in severities(added)
    assert "response header removed" in messages(removed)
    assert "breaking" in severities(removed)


def test_new_response_status_is_breaking() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["responses"]["202"] = {"description": "Accepted"}

    report = analyze_openapi_compatibility(baseline, current)

    assert "response status added" in messages(report)
    assert "breaking" in severities(report)


def test_newly_documented_framework_500_is_metadata() -> None:
    baseline = document()
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="500",
        codes=["INTERNAL_SERVER_ERROR"],
        sources=["framework"],
    )

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is True
    assert severities(report) == ["metadata"]
    assert messages(report) == ["framework error response documented"]


def test_arbitrary_new_500_remains_breaking() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["responses"]["500"] = {
        "description": "Internal error"
    }

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert severities(report) == ["breaking"]
    assert messages(report) == ["response status added"]


@pytest.mark.parametrize(
    ("codes", "sources"),
    [
        (["INTERNAL_SERVER_ERROR", "APP_FAILURE"], ["app", "framework"]),
        (["INTERNAL_SERVER_ERROR"], ["app", "framework"]),
    ],
)
def test_new_500_with_application_behavior_remains_breaking(
    codes: list[str], sources: list[str]
) -> None:
    baseline = document()
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="500",
        codes=codes,
        sources=sources,
    )

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert severities(report) == ["breaking"]
    assert messages(report) == ["response status added"]


@pytest.mark.parametrize("header_name", ["Retry-After", "X-Tenchi-Error-Source"])
def test_new_500_with_noncanonical_headers_remains_breaking(
    header_name: str,
) -> None:
    baseline = document()
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="500",
        codes=["INTERNAL_SERVER_ERROR"],
        sources=["framework"],
    )
    current["paths"]["/items"]["post"]["responses"]["500"]["headers"][header_name] = {
        "schema": {"type": "string"}
    }

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert severities(report) == ["breaking"]
    assert messages(report) == ["response status added"]


def test_new_500_with_malformed_error_envelope_remains_breaking() -> None:
    baseline = document()
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="500",
        codes=["INTERNAL_SERVER_ERROR"],
        sources=["framework"],
    )
    current["components"]["schemas"]["ErrorResponse"]["required"].append("code")

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert severities(report) == ["breaking"]
    assert messages(report) == ["response status added"]


def test_new_500_with_unsupported_source_header_fields_remains_breaking() -> None:
    baseline = document()
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="500",
        codes=["INTERNAL_SERVER_ERROR"],
        sources=["framework"],
    )
    current["paths"]["/items"]["post"]["responses"]["500"]["headers"][
        "x-tenchi-error-source"
    ]["explode"] = True

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert severities(report) == ["breaking"]
    assert messages(report) == ["response status added"]


def test_generic_error_schema_can_be_replaced_by_exact_codes() -> None:
    baseline = document()
    baseline["components"]["schemas"]["ErrorResponse"] = {
        "title": "ErrorResponse",
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "details": {},
            "request_id": {"type": "string"},
        },
        "required": ["code", "message"],
    }
    baseline["paths"]["/items"]["post"]["responses"]["409"] = {
        "description": "ITEM_CONFLICT",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"}
            }
        },
    }
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="409",
        codes=["ITEM_CONFLICT"],
        sources=["app"],
    )

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is True
    assert "error codes documented" in messages(report)
    assert "response header added" in messages(report)


def test_exact_error_code_changes_are_directional() -> None:
    baseline = document()
    add_tenchi_error_response(
        baseline,
        status="409",
        codes=["ITEM_CONFLICT"],
        sources=["app"],
    )
    current = deepcopy(baseline)
    add_tenchi_error_response(
        current,
        status="409",
        codes=["ITEM_CONFLICT", "ITEM_LOCKED"],
        sources=["app"],
    )

    added = analyze_openapi_compatibility(baseline, current)
    removed = analyze_openapi_compatibility(current, baseline)

    assert "breaking" in severities(added)
    assert "error code added" in messages(added)
    assert "additive" in severities(removed)
    assert "error code removed" in messages(removed)


def test_exact_error_source_changes_are_directional() -> None:
    baseline = document()
    add_tenchi_error_response(
        baseline,
        status="409",
        codes=["ITEM_CONFLICT"],
        sources=["app"],
    )
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["responses"]["409"]["headers"][
        "x-tenchi-error-source"
    ]["schema"]["enum"].append("framework")

    added = analyze_openapi_compatibility(baseline, current)
    removed = analyze_openapi_compatibility(current, baseline)

    assert added.compatible is False
    assert "breaking" in severities(added)
    assert "enum values changed" in messages(added)
    assert removed.compatible is True
    assert "additive" in severities(removed)


def test_unknown_exact_error_constraints_fail_closed() -> None:
    baseline = document()
    add_tenchi_error_response(
        baseline,
        status="409",
        codes=["ITEM_CONFLICT"],
        sources=["app"],
    )
    current = deepcopy(baseline)
    restriction = current["paths"]["/items"]["post"]["responses"]["409"]["content"][
        "application/json"
    ]["schema"]["allOf"][1]
    restriction["properties"]["message"] = {"type": "string"}

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "unknown" in severities(report)


@pytest.mark.parametrize("baseline_exact", [False, True])
def test_error_envelope_ref_siblings_fail_closed(baseline_exact: bool) -> None:
    baseline = document()
    add_tenchi_error_response(
        baseline,
        status="409",
        codes=["ITEM_CONFLICT"],
        sources=["app"],
    )
    if not baseline_exact:
        baseline["paths"]["/items"]["post"]["responses"]["409"]["content"][
            "application/json"
        ]["schema"] = {"$ref": "#/components/schemas/ErrorResponse"}
    current = deepcopy(baseline)
    schema = current["paths"]["/items"]["post"]["responses"]["409"]["content"][
        "application/json"
    ]["schema"]
    target = schema["allOf"][0] if baseline_exact else schema
    target["not"] = {}

    report = analyze_openapi_compatibility(baseline, current)

    assert report.compatible is False
    assert "unknown" in severities(report)


def test_metadata_is_safe_and_unsupported_changes_require_review() -> None:
    baseline = document()
    current = deepcopy(baseline)
    operation = current["paths"]["/items"]["post"]
    operation["summary"] = "Create"
    operation["x-vendor-behavior"] = True

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "review required"
    assert severities(report) == ["metadata", "unknown"]


def test_webhook_verification_requirement_is_directional() -> None:
    baseline = document()
    required = deepcopy(baseline)
    required["paths"]["/items"]["post"]["x-tenchi-webhook"] = True

    tightened = analyze_openapi_compatibility(baseline, required)
    relaxed = analyze_openapi_compatibility(required, baseline)

    assert tightened.status == "incompatible"
    assert messages(tightened) == ["webhook verification became required"]
    assert relaxed.status == "compatible"
    assert messages(relaxed) == ["webhook verification requirement was removed"]


def test_malformed_webhook_extension_change_fails_closed() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["x-tenchi-webhook"] = "sometimes"

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "review required"
    assert messages(report) == ["webhook verification requirement changed"]


def test_idempotency_guarantee_is_directional() -> None:
    baseline = document()
    baseline["paths"]["/items"]["post"]["parameters"].append(
        {
            "in": "header",
            "name": "idempotency-key",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
        }
    )
    guaranteed = deepcopy(baseline)
    guaranteed["paths"]["/items"]["post"]["x-tenchi-idempotency-key"] = (
        "Idempotency-Key"
    )

    added = analyze_openapi_compatibility(baseline, guaranteed)
    removed = analyze_openapi_compatibility(guaranteed, baseline)

    assert added.status == "compatible"
    assert messages(added) == ["idempotency guarantee documented"]
    assert removed.status == "incompatible"
    assert messages(removed) == ["idempotency guarantee removed"]


def test_generated_referenced_idempotency_header_is_recognized_as_additive() -> None:
    class Key(StrEnum):
        FIXED = "fixed"

    class ReferencedKeyHeaders(BaseModel):
        idempotency_key: Annotated[Key, Field(min_length=1)]

    @dataclass(frozen=True, slots=True)
    class Context:
        pass

    async def command(headers: ReferencedKeyHeaders, context: Context) -> None:
        return None

    declared = contract(
        method="POST",
        path="/commands",
        headers=ReferencedKeyHeaders,
        status=204,
        idempotency_key=True,
    )
    current = openapi_schema(
        route_group(route(declared, command)),
        title="Commands",
        version="1",
    )
    baseline = deepcopy(current)
    del baseline["paths"]["/commands"]["post"]["x-tenchi-idempotency-key"]

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "compatible"
    assert messages(report) == ["idempotency guarantee documented"]


def test_malformed_idempotency_extension_change_fails_closed() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["x-tenchi-idempotency-key"] = True

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "review required"
    assert messages(report) == ["idempotency guarantee changed"]


def test_idempotency_extension_without_its_required_header_fails_closed() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["x-tenchi-idempotency-key"] = "Idempotency-Key"

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "review required"
    assert messages(report) == ["idempotency guarantee changed"]


def test_openapi_examples_are_metadata() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["requestBody"]["content"]["application/json"][
        "examples"
    ] = {"create": {"value": {"name": "example"}}}
    current["paths"]["/items"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["examples"] = {"created": {"value": {"name": "example"}}}

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "compatible"
    assert severities(report) == ["metadata", "metadata"]
    assert messages(report) == ["examples changed", "examples changed"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param("root", "unsupported document fields changed", id="root"),
        pytest.param("path", "unsupported path fields changed", id="path-item"),
        pytest.param(
            "ref-sibling", "schema reference siblings changed", id="ref-sibling"
        ),
    ],
)
def test_changed_fields_outside_tenchi_subset_fail_closed(
    change: str, message: str
) -> None:
    baseline = document()
    current = deepcopy(baseline)
    if change == "root":
        current["servers"] = [{"url": "https://example.test"}]
    elif change == "path":
        current["paths"]["/items"]["parameters"] = []
    else:
        current["paths"]["/items"]["post"]["responses"]["201"]["content"][
            "application/json"
        ]["schema"]["description"] = "An item"

    report = analyze_openapi_compatibility(baseline, current)

    assert report.status == "review required"
    assert message in messages(report)


def test_authentication_tightening_is_breaking_and_relaxing_is_additive() -> None:
    public = document()
    protected = deepcopy(public)
    protected["security"] = [{"bearerAuth": []}]

    tightened = analyze_openapi_compatibility(public, protected)
    relaxed = analyze_openapi_compatibility(protected, public)

    assert "authentication became required" in messages(tightened)
    assert "breaking" in severities(tightened)
    assert "authentication requirement was removed" in messages(relaxed)
    assert "additive" in severities(relaxed)


def test_equivalent_public_security_declarations_do_not_report_a_change() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["paths"]["/items"]["post"]["security"] = []

    report = analyze_openapi_compatibility(baseline, current)

    assert report.changes == ()


def test_human_and_json_reports_have_stable_summary_data() -> None:
    baseline = document()
    current = deepcopy(baseline)
    current["info"]["version"] = "1.1.0"
    report = analyze_openapi_compatibility(baseline, current)

    rendered = render_compatibility_report(report, baseline_path="openapi.json")
    data = report.as_dict()

    assert "OpenAPI compatibility against openapi.json: compatible" in rendered
    assert "METADATA" in rendered
    assert data["compatible"] is True
    assert data["counts"] == {
        "breaking": 0,
        "additive": 0,
        "metadata": 1,
        "unknown": 0,
    }


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({}, id="empty"),
        pytest.param([], id="array"),
        pytest.param({"openapi": "3.0.3", "paths": {}}, id="openapi-3.0"),
        pytest.param({"openapi": "3.1.0"}, id="missing-paths"),
        pytest.param(
            {
                "openapi": "3.1.0",
                "info": {},
                "paths": {},
                "components": "schemas",
            },
            id="invalid-components",
        ),
        pytest.param(
            {
                "openapi": "3.1.0",
                "info": {},
                "paths": {"/items": {"get": "operation"}},
            },
            id="invalid-operation",
        ),
    ],
)
def test_invalid_documents_are_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        analyze_openapi_compatibility(value, document())
