"""Canonical rendering and drift diagnostics for OpenAPI snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping
from difflib import unified_diff
from typing import cast

type JsonObject = Mapping[str, object]
type OperationKey = tuple[str, str]

_HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)


def render_openapi_snapshot(document: Mapping[str, object]) -> str:
    """Render an OpenAPI document in the one canonical snapshot format."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def describe_openapi_drift(expected: object, actual: object) -> list[str]:
    """Describe meaningful OpenAPI changes in stable, review-friendly terms."""
    changes: list[str] = []
    expected_document = _object(expected)
    actual_document = _object(actual)

    if expected_document.get("openapi") != actual_document.get("openapi"):
        changes.append("OpenAPI version changed")
    if expected_document.get("info") != actual_document.get("info"):
        changes.append("API metadata changed")
    if expected_document.get("security") != actual_document.get("security"):
        changes.append("global security requirements changed")
    expected_components = _object(expected_document.get("components"))
    actual_components = _object(actual_document.get("components"))
    if expected_components.get("securitySchemes") != actual_components.get(
        "securitySchemes"
    ):
        changes.append("security schemes changed")

    expected_operations = _operations(expected_document)
    actual_operations = _operations(actual_document)
    expected_keys = set(expected_operations)
    actual_keys = set(actual_operations)

    for method, path in sorted(actual_keys - expected_keys):
        changes.append(f"operation added: {method.upper()} {path}")
    for method, path in sorted(expected_keys - actual_keys):
        changes.append(f"operation removed: {method.upper()} {path}")

    for method, path in sorted(expected_keys & actual_keys):
        label = f"{method.upper()} {path}"
        before = expected_operations[(method, path)]
        after = actual_operations[(method, path)]

        if before.get("parameters") != after.get("parameters"):
            changes.append(f"parameters changed: {label}")
        if before.get("requestBody") != after.get("requestBody"):
            changes.append(f"request body changed: {label}")
        if before.get("security") != after.get("security"):
            changes.append(f"security changed: {label}")

        before_responses = _object(before.get("responses"))
        after_responses = _object(after.get("responses"))
        before_statuses = set(before_responses)
        after_statuses = set(after_responses)
        for status in sorted(after_statuses - before_statuses):
            changes.append(f"response added: {label} -> {status}")
        for status in sorted(before_statuses - after_statuses):
            changes.append(f"response removed: {label} -> {status}")
        for status in sorted(before_statuses & after_statuses):
            if before_responses[status] != after_responses[status]:
                kind = "error response" if _is_error_status(status) else "response"
                changes.append(f"{kind} changed: {label} -> {status}")

        ignored = {"parameters", "requestBody", "responses", "security"}
        before_rest = {
            key: value for key, value in before.items() if key not in ignored
        }
        after_rest = {key: value for key, value in after.items() if key not in ignored}
        if before_rest != after_rest:
            changes.append(f"operation metadata changed: {label}")

    before_schemas = _component_schemas(expected_document)
    after_schemas = _component_schemas(actual_document)
    before_names = set(before_schemas)
    after_names = set(after_schemas)
    for name in sorted(after_names - before_names):
        changes.append(f"component schema added: {name}")
    for name in sorted(before_names - after_names):
        changes.append(f"component schema removed: {name}")
    for name in sorted(before_names & after_names):
        if before_schemas[name] != after_schemas[name]:
            changes.append(f"component schema changed: {name}")

    if not changes:
        changes.append("document structure or formatting changed")
    return changes


def openapi_snapshot_diff(expected: str, actual: str, *, snapshot_path: str) -> str:
    """Return a unified diff between a stored and generated snapshot."""
    return "".join(
        unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=snapshot_path,
            tofile="generated OpenAPI",
        )
    )


def _object(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    mapping = cast(Mapping[object, object], value)
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


def _operations(document: JsonObject) -> dict[OperationKey, JsonObject]:
    operations: dict[OperationKey, JsonObject] = {}
    for path, path_item in _object(document.get("paths")).items():
        for method, operation in _object(path_item).items():
            if method in _HTTP_METHODS:
                operations[(method, path)] = _object(operation)
    return operations


def _component_schemas(document: JsonObject) -> JsonObject:
    return _object(_object(document.get("components")).get("schemas"))


def _is_error_status(status: str) -> bool:
    return status == "default" or (status.isdigit() and int(status) >= 400)
