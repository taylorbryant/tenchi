"""Conservative compatibility analysis for Tenchi boundary documents.

The analyzer proves only a deliberately small set of changes safe. Anything
outside that set is reported as unknown so automation never mistakes
incomplete analysis for compatibility.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ._schema_compatibility import (
    ChangeSeverity,
    Direction,
    JsonObject,
    compare_schema,
)

type CompatibilityStatus = Literal["compatible", "incompatible", "review required"]
type OperationKey = tuple[str, str]

__all__ = [
    "CompatibilityChange",
    "CompatibilityReport",
    "analyze_openapi_compatibility",
    "analyze_tool_compatibility",
    "render_compatibility_report",
    "render_tool_compatibility_report",
]

_SEVERITIES: tuple[ChangeSeverity, ...] = (
    "breaking",
    "additive",
    "metadata",
    "unknown",
)
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
_ROOT_KNOWN = frozenset({"openapi", "info", "paths", "components", "security"})
_COMPONENTS_KNOWN = frozenset({"schemas", "securitySchemes"})
_OPERATION_METADATA = frozenset(
    {"summary", "description", "tags", "deprecated", "x-sunset"}
)
_OPERATION_KNOWN = frozenset(
    {
        "operationId",
        *_OPERATION_METADATA,
        "x-timeout-seconds",
        "x-tenchi-webhook",
        "parameters",
        "requestBody",
        "responses",
        "security",
    }
)


@dataclass(frozen=True, slots=True)
class CompatibilityChange:
    """One classified difference between baseline and current documents."""

    severity: ChangeSeverity
    location: str
    message: str


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """The complete, stable-order result of compatibility analysis."""

    changes: tuple[CompatibilityChange, ...]

    @property
    def compatible(self) -> bool:
        return not any(
            change.severity in ("breaking", "unknown") for change in self.changes
        )

    @property
    def status(self) -> CompatibilityStatus:
        if any(change.severity == "breaking" for change in self.changes):
            return "incompatible"
        if any(change.severity == "unknown" for change in self.changes):
            return "review required"
        return "compatible"

    def count(self, severity: ChangeSeverity) -> int:
        return sum(change.severity == severity for change in self.changes)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable report with stable keys."""
        return {
            "status": self.status,
            "compatible": self.compatible,
            "counts": {severity: self.count(severity) for severity in _SEVERITIES},
            "changes": [
                {
                    "severity": change.severity,
                    "location": change.location,
                    "message": change.message,
                }
                for change in self.changes
            ],
        }


class _Collector:
    def __init__(self) -> None:
        self.values: list[CompatibilityChange] = []
        self._seen: set[tuple[str, str, str]] = set()

    def add(self, severity: ChangeSeverity, location: str, message: str) -> None:
        key = (severity, location, message)
        if key not in self._seen:
            self._seen.add(key)
            self.values.append(CompatibilityChange(severity, location, message))


class _Analyzer:
    def __init__(self, baseline: JsonObject, current: JsonObject) -> None:
        self.baseline = baseline
        self.current = current
        self.changes = _Collector()

    def run(self) -> CompatibilityReport:
        if _field_changed(self.baseline, self.current, "info"):
            self.changes.add("metadata", "document", "API metadata changed")

        before_components = _object(self.baseline.get("components"))
        after_components = _object(self.current.get("components"))
        if _field_changed(before_components, after_components, "securitySchemes"):
            self.changes.add(
                "unknown", "document security", "security scheme definitions changed"
            )
        self._unknown_fields(
            self.baseline,
            self.current,
            known=_ROOT_KNOWN,
            location="document",
            message="unsupported document fields changed",
        )
        self._unknown_fields(
            before_components,
            after_components,
            known=_COMPONENTS_KNOWN,
            location="document components",
            message="unsupported component fields changed",
        )
        self._compare_path_fields()

        before_operations = _operations(self.baseline)
        after_operations = _operations(self.current)
        before_keys = set(before_operations)
        after_keys = set(after_operations)
        for method, path in sorted(after_keys - before_keys, key=_operation_sort_key):
            self.changes.add(
                "additive", _operation_label(method, path), "operation added"
            )
        for method, path in sorted(before_keys - after_keys, key=_operation_sort_key):
            self.changes.add(
                "breaking", _operation_label(method, path), "operation removed"
            )
        for method, path in sorted(before_keys & after_keys, key=_operation_sort_key):
            self._compare_operation(
                before_operations[(method, path)],
                after_operations[(method, path)],
                location=_operation_label(method, path),
            )
        return CompatibilityReport(tuple(self.changes.values))

    def _compare_path_fields(self) -> None:
        before_paths = _object(self.baseline.get("paths"))
        after_paths = _object(self.current.get("paths"))
        for path in sorted(set(before_paths) | set(after_paths)):
            before = _object(before_paths.get(path))
            after = _object(after_paths.get(path))
            self._unknown_fields(
                before,
                after,
                known=_HTTP_METHODS,
                location=f"path {path}",
                message="unsupported path fields changed",
            )

    def _compare_operation(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        if _field_changed(before, after, "operationId"):
            self.changes.add("breaking", location, "operation id changed")
        if any(_field_changed(before, after, key) for key in _OPERATION_METADATA):
            self.changes.add("metadata", location, "operation metadata changed")
        if _field_changed(before, after, "x-timeout-seconds"):
            self.changes.add("unknown", location, "request timeout changed")
        self._compare_webhook_requirement(before, after, location=location)

        self._compare_security(
            _effective_security(self.baseline, before),
            _effective_security(self.current, after),
            location=location,
        )
        self._compare_parameters(
            before.get("parameters"), after.get("parameters"), location=location
        )
        self._compare_request_body(
            before.get("requestBody"), after.get("requestBody"), location=location
        )
        self._compare_responses(
            before.get("responses"), after.get("responses"), location=location
        )
        self._unknown_fields(
            before,
            after,
            known=_OPERATION_KNOWN,
            location=location,
            message="unsupported operation fields changed",
        )

    def _compare_webhook_requirement(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
    ) -> None:
        if not _field_changed(before, after, "x-tenchi-webhook"):
            return
        before_value = before.get("x-tenchi-webhook", False)
        after_value = after.get("x-tenchi-webhook", False)
        if not isinstance(before_value, bool) or not isinstance(after_value, bool):
            self.changes.add(
                "unknown",
                location,
                "webhook verification requirement changed",
            )
        elif not before_value and after_value:
            self.changes.add(
                "breaking",
                location,
                "webhook verification became required",
            )
        elif before_value and not after_value:
            self.changes.add(
                "additive",
                location,
                "webhook verification requirement was removed",
            )

    def _compare_security(
        self, before: object, after: object, *, location: str
    ) -> None:
        if before == after:
            return
        before_public = _is_public_security(before)
        after_public = _is_public_security(after)
        if before_public and after_public:
            return
        if before_public:
            self.changes.add("breaking", location, "authentication became required")
        elif after_public:
            self.changes.add(
                "additive", location, "authentication requirement was removed"
            )
        else:
            self.changes.add("unknown", location, "authentication requirements changed")

    def _compare_parameters(
        self, before_value: object, after_value: object, *, location: str
    ) -> None:
        before = _parameters(before_value)
        after = _parameters(after_value)
        if before is None or after is None:
            if before_value != after_value:
                self.changes.add(
                    "unknown", location, "unsupported parameter structure changed"
                )
            return

        before_keys = set(before)
        after_keys = set(after)
        for key in sorted(after_keys - before_keys):
            parameter = after[key]
            parameter_location = _parameter_location(location, key)
            if parameter.get("required") is True:
                self.changes.add(
                    "breaking", parameter_location, "required parameter added"
                )
            else:
                self.changes.add(
                    "additive", parameter_location, "optional parameter added"
                )
        for key in sorted(before_keys - after_keys):
            self.changes.add(
                "breaking", _parameter_location(location, key), "parameter removed"
            )
        for key in sorted(before_keys & after_keys):
            self._compare_parameter(before[key], after[key], location, key)

    def _compare_parameter(
        self,
        before: JsonObject,
        after: JsonObject,
        operation_location: str,
        key: tuple[str, str],
    ) -> None:
        location = _parameter_location(operation_location, key)
        before_required = before.get("required") is True
        after_required = after.get("required") is True
        if not before_required and after_required:
            self.changes.add("breaking", location, "parameter became required")
        elif before_required and not after_required:
            self.changes.add("additive", location, "parameter became optional")
        self._schema(
            before.get("schema"),
            after.get("schema"),
            direction="input",
            location=location,
        )
        if any(
            _field_changed(before, after, field)
            for field in ("description", "deprecated")
        ):
            self.changes.add("metadata", location, "parameter metadata changed")
        self._unknown_fields(
            before,
            after,
            known={"name", "in", "required", "schema", "description", "deprecated"},
            location=location,
            message="unsupported parameter fields changed",
        )

    def _compare_request_body(
        self, before_value: object, after_value: object, *, location: str
    ) -> None:
        before = _object(before_value)
        after = _object(after_value)
        body_location = f"{location} request body"
        if not before and not after:
            if before_value != after_value:
                self.changes.add("unknown", body_location, "request body shape changed")
            return
        if not before:
            severity: ChangeSeverity = (
                "breaking" if after.get("required") is True else "additive"
            )
            self.changes.add(severity, body_location, "request body added")
            return
        if not after:
            self.changes.add("breaking", body_location, "request body removed")
            return

        before_required = before.get("required") is True
        after_required = after.get("required") is True
        if not before_required and after_required:
            self.changes.add("breaking", body_location, "request body became required")
        elif before_required and not after_required:
            self.changes.add("additive", body_location, "request body became optional")
        self._compare_content(
            before.get("content"),
            after.get("content"),
            direction="input",
            location=body_location,
        )
        if _field_changed(before, after, "description"):
            self.changes.add("metadata", body_location, "request body metadata changed")
        self._unknown_fields(
            before,
            after,
            known={"required", "content", "description"},
            location=body_location,
            message="unsupported request body fields changed",
        )

    def _compare_responses(
        self, before_value: object, after_value: object, *, location: str
    ) -> None:
        before = _object(before_value)
        after = _object(after_value)
        if not before or not after:
            if before_value != after_value:
                self.changes.add("breaking", location, "response set changed")
            return
        before_statuses = set(before)
        after_statuses = set(after)
        for status in sorted(after_statuses - before_statuses):
            self.changes.add(
                "breaking", f"{location} response {status}", "response status added"
            )
        for status in sorted(before_statuses - after_statuses):
            self.changes.add(
                "breaking", f"{location} response {status}", "response status removed"
            )
        for status in sorted(before_statuses & after_statuses):
            self._compare_response(
                _object(before[status]),
                _object(after[status]),
                location=f"{location} response {status}",
                error=_is_error_status(status),
            )

    def _compare_response(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
        error: bool,
    ) -> None:
        if _field_changed(before, after, "description"):
            self.changes.add(
                "breaking" if error else "metadata",
                location,
                "error contract changed" if error else "response description changed",
            )
        self._compare_content(
            before.get("content"),
            after.get("content"),
            direction="output",
            location=location,
        )
        self._compare_headers(
            before.get("headers"), after.get("headers"), location=location
        )
        self._unknown_fields(
            before,
            after,
            known={"description", "content", "headers"},
            location=location,
            message="unsupported response fields changed",
        )

    def _compare_headers(
        self, before_value: object, after_value: object, *, location: str
    ) -> None:
        before = _object(before_value)
        after = _object(after_value)
        before_names = set(before)
        after_names = set(after)
        for name in sorted(after_names - before_names, key=str.casefold):
            self.changes.add(
                "additive", f"{location} header {name!r}", "response header added"
            )
        for name in sorted(before_names - after_names, key=str.casefold):
            self.changes.add(
                "breaking", f"{location} header {name!r}", "response header removed"
            )
        for name in sorted(before_names & after_names, key=str.casefold):
            self._compare_header(
                _object(before[name]),
                _object(after[name]),
                location=f"{location} header {name!r}",
            )

    def _compare_header(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        before_required = before.get("required") is True
        after_required = after.get("required") is True
        if before_required and not after_required:
            self.changes.add("breaking", location, "response header became optional")
        elif not before_required and after_required:
            self.changes.add("additive", location, "response header became required")
        self._schema(
            before.get("schema"),
            after.get("schema"),
            direction="output",
            location=location,
        )
        if any(
            _field_changed(before, after, field)
            for field in ("description", "deprecated")
        ):
            self.changes.add("metadata", location, "response header metadata changed")
        self._unknown_fields(
            before,
            after,
            known={"required", "schema", "description", "deprecated"},
            location=location,
            message="unsupported response header fields changed",
        )

    def _compare_content(
        self,
        before_value: object,
        after_value: object,
        *,
        direction: Direction,
        location: str,
    ) -> None:
        before = _object(before_value)
        after = _object(after_value)
        before_types = set(before)
        after_types = set(after)
        for media_type in sorted(after_types - before_types):
            severity: ChangeSeverity = (
                "additive" if direction == "input" else "breaking"
            )
            self.changes.add(severity, location, f"media type added: {media_type}")
        for media_type in sorted(before_types - after_types):
            self.changes.add("breaking", location, f"media type removed: {media_type}")
        for media_type in sorted(before_types & after_types):
            before_media = _object(before[media_type])
            after_media = _object(after[media_type])
            media_location = f"{location} {media_type}"
            self._schema(
                before_media.get("schema"),
                after_media.get("schema"),
                direction=direction,
                location=media_location,
            )
            self._unknown_fields(
                before_media,
                after_media,
                known={"schema"},
                location=media_location,
                message="unsupported media fields changed",
            )

    def _schema(
        self,
        before: object,
        after: object,
        *,
        direction: Direction,
        location: str,
    ) -> None:
        compare_schema(
            before,
            after,
            direction=direction,
            baseline=self.baseline,
            current=self.current,
            location=location,
            changes=self.changes,
        )

    def _unknown_fields(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        known: set[str] | frozenset[str],
        location: str,
        message: str,
    ) -> None:
        unknown = (set(before) | set(after)) - known
        if any(_field_changed(before, after, key) for key in unknown):
            self.changes.add("unknown", location, message)


def analyze_openapi_compatibility(
    baseline: object, current: object
) -> CompatibilityReport:
    """Classify changes from baseline to current conservatively.

    Both inputs must be OpenAPI 3.1 object documents. The supported dialect is
    the subset emitted by :func:`tenchi.openapi.openapi_schema`; changed
    constructs outside that subset are reported as unknown.
    """
    before = _document(baseline, label="baseline")
    after = _document(current, label="current")
    return _Analyzer(before, after).run()


class _ToolAnalyzer:
    def __init__(self, baseline: JsonObject, current: JsonObject) -> None:
        self.baseline = baseline
        self.current = current
        self.changes = _Collector()

    def run(self) -> CompatibilityReport:
        before_version = self.baseline["schema_version"]
        after_version = self.current["schema_version"]
        if before_version != after_version:
            self.changes.add(
                "unknown",
                "tool manifest",
                "manifest schema version changed",
            )
            return CompatibilityReport(tuple(self.changes.values))

        before_tools = _tools_by_name(self.baseline)
        after_tools = _tools_by_name(self.current)
        before_names = set(before_tools)
        after_names = set(after_tools)
        for name in sorted(after_names - before_names):
            self.changes.add("additive", f"tool {name!r}", "tool added")
        for name in sorted(before_names - after_names):
            self.changes.add("breaking", f"tool {name!r}", "tool removed")
        for name in sorted(before_names & after_names):
            self._compare_tool(
                before_tools[name],
                after_tools[name],
                location=f"tool {name!r}",
            )

        known = {"schema_version", "tools"}
        if any(
            _field_changed(self.baseline, self.current, key)
            for key in (set(self.baseline) | set(self.current)) - known
        ):
            self.changes.add(
                "unknown",
                "tool manifest",
                "unsupported manifest fields changed",
            )
        return CompatibilityReport(tuple(self.changes.values))

    def _compare_tool(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
    ) -> None:
        if _field_changed(before, after, "description"):
            self.changes.add("metadata", location, "description changed")

        before_input = _object(before["input_schema"])
        after_input = _object(after["input_schema"])
        compare_schema(
            before_input,
            after_input,
            direction="input",
            baseline=before_input,
            current=after_input,
            location=f"{location} input",
            changes=self.changes,
        )
        before_output = _object(before["output_schema"])
        after_output = _object(after["output_schema"])
        compare_schema(
            before_output,
            after_output,
            direction="output",
            baseline=before_output,
            current=after_output,
            location=f"{location} output",
            changes=self.changes,
        )
        self._compare_errors(
            _tool_errors(before),
            _tool_errors(after),
            location=location,
        )
        self._compare_annotations(
            _object(before["annotations"]),
            _object(after["annotations"]),
            location=location,
        )

        known = {
            "name",
            "description",
            "input_schema",
            "output_schema",
            "errors",
            "annotations",
        }
        if any(
            _field_changed(before, after, key)
            for key in (set(before) | set(after)) - known
        ):
            self.changes.add(
                "unknown",
                location,
                "unsupported tool fields changed",
            )

    def _compare_errors(
        self,
        before: dict[str, JsonObject],
        after: dict[str, JsonObject],
        *,
        location: str,
    ) -> None:
        before_codes = set(before)
        after_codes = set(after)
        for code in sorted(after_codes - before_codes):
            self.changes.add(
                "breaking",
                f"{location} error {code!r}",
                "application error added",
            )
        for code in sorted(before_codes - after_codes):
            self.changes.add(
                "additive",
                f"{location} error {code!r}",
                "application error removed",
            )
        for code in sorted(before_codes & after_codes):
            error_location = f"{location} error {code!r}"
            before_error = before[code]
            after_error = after[code]
            if _field_changed(before_error, after_error, "message"):
                self.changes.add(
                    "metadata",
                    error_location,
                    "error message changed",
                )
            if any(
                _field_changed(before_error, after_error, key)
                for key in (set(before_error) | set(after_error)) - {"code", "message"}
            ):
                self.changes.add(
                    "unknown",
                    error_location,
                    "unsupported error fields changed",
                )

    def _compare_annotations(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
    ) -> None:
        safer_value = {
            "read_only": True,
            "destructive": False,
            "idempotent": True,
            "open_world": False,
        }
        for name, safe in safer_value.items():
            before_value = before[name]
            after_value = after[name]
            if before_value == after_value:
                continue
            severity: ChangeSeverity = "additive" if after_value == safe else "breaking"
            self.changes.add(
                severity,
                f"{location} annotation {name!r}",
                f"changed from {str(before_value).lower()} "
                f"to {str(after_value).lower()}",
            )
        known = set(safer_value)
        if any(
            _field_changed(before, after, key)
            for key in (set(before) | set(after)) - known
        ):
            self.changes.add(
                "unknown",
                f"{location} annotations",
                "unsupported safety annotations changed",
            )


def analyze_tool_compatibility(
    baseline: object,
    current: object,
) -> CompatibilityReport:
    """Classify changes between versioned application-tool manifests.

    Input schemas are compared as values accepted by the current application;
    output schemas are compared as values existing consumers must continue to
    understand. Unsupported changes fail closed as unknown.
    """
    before = _tool_manifest_document(baseline, label="baseline")
    after = _tool_manifest_document(current, label="current")
    return _ToolAnalyzer(before, after).run()


def render_compatibility_report(
    report: CompatibilityReport, *, baseline_path: str
) -> str:
    """Render a concise human-readable compatibility report."""
    counts = ", ".join(
        f"{report.count(severity)} {severity}" for severity in _SEVERITIES
    )
    lines = [
        f"OpenAPI compatibility against {baseline_path}: {report.status}",
        counts,
    ]
    if not report.changes:
        lines.append("No API changes found.")
        return "\n".join(lines) + "\n"
    for severity in _SEVERITIES:
        group = [change for change in report.changes if change.severity == severity]
        if group:
            lines.extend(("", severity.upper()))
            lines.extend(f"  - {change.location}: {change.message}" for change in group)
    return "\n".join(lines) + "\n"


def render_tool_compatibility_report(
    report: CompatibilityReport,
    *,
    baseline_path: str,
) -> str:
    """Render a concise human-readable application-tool compatibility report."""
    counts = ", ".join(
        f"{report.count(severity)} {severity}" for severity in _SEVERITIES
    )
    lines = [
        f"Tool compatibility against {baseline_path}: {report.status}",
        counts,
    ]
    if not report.changes:
        lines.append("No tool changes found.")
        return "\n".join(lines) + "\n"
    for severity in _SEVERITIES:
        group = [change for change in report.changes if change.severity == severity]
        if group:
            lines.extend(("", severity.upper()))
            lines.extend(f"  - {change.location}: {change.message}" for change in group)
    return "\n".join(lines) + "\n"


def _document(value: object, *, label: str) -> JsonObject:
    document = _object(value)
    if not document:
        raise ValueError(f"{label} must be an OpenAPI object document")
    if document.get("openapi") != "3.1.0":
        raise ValueError(f"{label} must be an OpenAPI 3.1.0 document")
    if not isinstance(document.get("info"), Mapping):
        raise ValueError(f"{label} OpenAPI document must contain an info object")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError(f"{label} OpenAPI document must contain a paths object")
    components = document.get("components")
    if components is not None and not isinstance(components, Mapping):
        raise ValueError(f"{label} OpenAPI components must be an object")
    component_map = _object(cast(object, components))
    for key in ("schemas", "securitySchemes"):
        if key in component_map and not isinstance(component_map[key], Mapping):
            raise ValueError(f"{label} OpenAPI components.{key} must be an object")
    for path, path_item_value in _object(cast(object, paths)).items():
        if not isinstance(path_item_value, Mapping):
            raise ValueError(f"{label} OpenAPI path {path!r} must be an object")
        path_item = _object(cast(object, path_item_value))
        for method in _HTTP_METHODS & set(path_item):
            if not isinstance(path_item[method], Mapping):
                raise ValueError(
                    f"{label} OpenAPI operation {method.upper()} {path} "
                    "must be an object"
                )
    return document


def _tool_manifest_document(value: object, *, label: str) -> JsonObject:
    document = _object(value)
    if not document:
        raise ValueError(f"{label} must be a tool manifest object")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
        raise ValueError(
            f"{label} tool manifest must contain a positive schema_version"
        )
    raw_tools = document.get("tools")
    if not isinstance(raw_tools, Sequence) or isinstance(raw_tools, str | bytes):
        raise ValueError(f"{label} tool manifest must contain a tools array")

    names: set[str] = set()
    for index, value in enumerate(cast(Sequence[object], raw_tools)):
        tool = _object(value)
        name = tool.get("name")
        if not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None:
            raise ValueError(
                f"{label} tool manifest tools[{index}].name must use dotted snake_case"
            )
        if name in names:
            raise ValueError(f"{label} tool manifest repeats tool {name!r}")
        names.add(name)
        description = tool.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(
                f"{label} tool manifest tool {name!r} must contain a "
                "non-empty description"
            )
        for field in ("input_schema", "output_schema", "annotations"):
            if not isinstance(tool.get(field), Mapping):
                raise ValueError(
                    f"{label} tool manifest tool {name!r} {field} must be an object"
                )
        for field in ("input_schema", "output_schema"):
            try:
                Draft202012Validator.check_schema(_object(tool[field]))
            except SchemaError as exc:
                raise ValueError(
                    f"{label} tool manifest tool {name!r} {field} "
                    f"is not valid JSON Schema: {exc}"
                ) from exc
        _validate_tool_errors(tool, label=label, name=name)
        annotations = _object(tool["annotations"])
        for annotation in (
            "read_only",
            "destructive",
            "idempotent",
            "open_world",
        ):
            if not isinstance(annotations.get(annotation), bool):
                raise ValueError(
                    f"{label} tool manifest tool {name!r} annotation "
                    f"{annotation!r} must be boolean"
                )
        if annotations["read_only"] is True and (
            annotations["destructive"] is True or annotations["idempotent"] is not True
        ):
            raise ValueError(
                f"{label} tool manifest tool {name!r} has inconsistent "
                "read-only safety annotations"
            )
    return document


def _validate_tool_errors(tool: JsonObject, *, label: str, name: str) -> None:
    raw_errors = tool.get("errors")
    if not isinstance(raw_errors, Sequence) or isinstance(raw_errors, str | bytes):
        raise ValueError(f"{label} tool manifest tool {name!r} errors must be an array")
    codes: set[str] = set()
    for index, value in enumerate(cast(Sequence[object], raw_errors)):
        error = _object(value)
        code = error.get("code")
        message = error.get("message")
        if not isinstance(code, str) or not code or not isinstance(message, str):
            raise ValueError(
                f"{label} tool manifest tool {name!r} errors[{index}] "
                "must contain string code and message values"
            )
        if code in codes:
            raise ValueError(
                f"{label} tool manifest tool {name!r} repeats error {code!r}"
            )
        codes.add(code)


def _tools_by_name(document: JsonObject) -> dict[str, JsonObject]:
    return {
        cast(str, tool["name"]): tool
        for value in cast(Sequence[object], document["tools"])
        if (tool := _object(value))
    }


def _tool_errors(tool: JsonObject) -> dict[str, JsonObject]:
    return {
        cast(str, error["code"]): error
        for value in cast(Sequence[object], tool["errors"])
        if (error := _object(value))
    }


def _parameters(value: object) -> dict[tuple[str, str], JsonObject] | None:
    if value is None:
        return {}
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    result: dict[tuple[str, str], JsonObject] = {}
    for item in cast(Sequence[object], value):
        parameter = _object(item)
        name = parameter.get("name")
        location = parameter.get("in")
        if not isinstance(name, str) or not isinstance(location, str):
            return None
        key = (location, name)
        if key in result:
            return None
        result[key] = parameter
    return result


def _effective_security(document: JsonObject, operation: JsonObject) -> object:
    return (
        operation.get("security")
        if "security" in operation
        else document.get("security")
    )


def _is_public_security(value: object) -> bool:
    return value is None or value == []


def _operations(document: JsonObject) -> dict[OperationKey, JsonObject]:
    operations: dict[OperationKey, JsonObject] = {}
    for path, path_item in _object(document.get("paths")).items():
        for method, operation in _object(path_item).items():
            if method in _HTTP_METHODS:
                operations[(method, path)] = _object(operation)
    return operations


def _object(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    mapping = cast(Mapping[object, object], value)
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


def _field_changed(before: JsonObject, after: JsonObject, key: str) -> bool:
    return (key in before) != (key in after) or before.get(key) != after.get(key)


def _parameter_location(operation: str, key: tuple[str, str]) -> str:
    location, name = key
    return f"{operation} {location} parameter {name!r}"


def _operation_label(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def _operation_sort_key(key: OperationKey) -> tuple[str, str]:
    method, path = key
    return path, method


def _is_error_status(status: str) -> bool:
    return status == "default" or (status.isdigit() and int(status) >= 400)
