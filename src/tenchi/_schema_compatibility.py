"""Directional JSON Schema comparison used by OpenAPI compatibility checks."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, cast

type ChangeSeverity = Literal["breaking", "additive", "metadata", "unknown"]
type Direction = Literal["input", "output"]
type JsonObject = Mapping[str, object]

_SCHEMA_METADATA = frozenset(
    {"title", "description", "examples", "example", "$comment"}
)
_LOWER_BOUNDS = (
    "minimum",
    "exclusiveMinimum",
    "minLength",
    "minItems",
    "minProperties",
)
_UPPER_BOUNDS = (
    "maximum",
    "exclusiveMaximum",
    "maxLength",
    "maxItems",
    "maxProperties",
)
_OPAQUE_RESTRICTIONS = ("pattern", "format", "multipleOf", "uniqueItems")
_SCHEMA_KNOWN = frozenset(
    {
        "$ref",
        "type",
        "enum",
        "const",
        "properties",
        "required",
        "items",
        "additionalProperties",
        "anyOf",
        "oneOf",
        "default",
        *_SCHEMA_METADATA,
        *_LOWER_BOUNDS,
        *_UPPER_BOUNDS,
        *_OPAQUE_RESTRICTIONS,
    }
)


class ChangeSink(Protocol):
    """Minimal destination needed by the schema comparator."""

    def add(self, severity: ChangeSeverity, location: str, message: str) -> None: ...


class _Comparator:
    def __init__(
        self,
        *,
        baseline: JsonObject,
        current: JsonObject,
        changes: ChangeSink,
        direction: Direction,
    ) -> None:
        self.baseline = baseline
        self.current = current
        self.changes = changes
        self.direction = direction

    def compare(
        self,
        before_value: object,
        after_value: object,
        *,
        location: str,
        visited: frozenset[str] = frozenset(),
    ) -> None:
        if not isinstance(before_value, Mapping) or not isinstance(
            after_value, Mapping
        ):
            if before_value != after_value:
                self.changes.add("unknown", location, "schema shape changed")
            return
        before = _object(cast(object, before_value))
        after = _object(cast(object, after_value))

        before_ref = before.get("$ref")
        after_ref = after.get("$ref")
        if "$ref" in before or "$ref" in after:
            siblings = (set(before) | set(after)) - {"$ref"}
            if any(
                _field_changed(before, after, key)
                or _expanded_value(self.baseline, before.get(key))
                != _expanded_value(self.current, after.get(key))
                for key in siblings
            ):
                self.changes.add(
                    "unknown", location, "schema reference siblings changed"
                )
            self._compare_reference(
                before_ref, after_ref, location=location, visited=visited
            )
            return
        if any(_field_changed(before, after, key) for key in _SCHEMA_METADATA):
            self.changes.add("metadata", location, "schema metadata changed")
        self._compare_type(before, after, location=location)
        self._compare_enum(before, after, location=location)
        self._compare_const(before, after, location=location)
        for keyword in ("anyOf", "oneOf"):
            self._compare_variants(
                before,
                after,
                keyword=keyword,
                location=location,
                visited=visited,
            )
        self._compare_object(before, after, location=location, visited=visited)
        self._compare_items(before, after, location=location, visited=visited)
        self._compare_bounds(before, after, location=location)
        self._compare_opaque(before, after, location=location)
        self._compare_additional_properties(
            before, after, location=location, visited=visited
        )
        if _field_changed(before, after, "default"):
            severity: ChangeSeverity = (
                "breaking" if self.direction == "input" else "unknown"
            )
            self.changes.add(severity, location, "default value changed")
        unknown = (set(before) | set(after)) - _SCHEMA_KNOWN
        if any(
            _field_changed(before, after, key)
            or _expanded_value(self.baseline, before.get(key))
            != _expanded_value(self.current, after.get(key))
            for key in unknown
        ):
            self.changes.add("unknown", location, "unsupported schema keywords changed")

    def _compare_reference(
        self,
        before_ref: object,
        after_ref: object,
        *,
        location: str,
        visited: frozenset[str],
    ) -> None:
        if (
            not isinstance(before_ref, str)
            or not isinstance(after_ref, str)
            or before_ref != after_ref
        ):
            self.changes.add("breaking", location, "schema reference changed")
            return
        if before_ref in visited:
            return
        before = _resolve_reference(self.baseline, before_ref)
        after = _resolve_reference(self.current, after_ref)
        if before is None or after is None:
            self.changes.add(
                "unknown", location, "schema reference could not be resolved"
            )
            return
        self.compare(before, after, location=location, visited=visited | {before_ref})

    def _compare_type(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        if not _field_changed(before, after, "type"):
            return
        before_types = _string_set(before.get("type")) if "type" in before else None
        after_types = _string_set(after.get("type")) if "type" in after else None
        if (before_types is None and "type" in before) or (
            after_types is None and "type" in after
        ):
            self.changes.add("unknown", location, "schema type changed")
        elif before_types is None:
            self._restriction_added(location, "schema type")
        elif after_types is None:
            self._restriction_removed(location, "schema type")
        else:
            self._classify_set(before_types, after_types, location, "schema type")

    def _compare_enum(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        if not _field_changed(before, after, "enum"):
            return
        before_enum = _canonical_set(before.get("enum")) if "enum" in before else None
        after_enum = _canonical_set(after.get("enum")) if "enum" in after else None
        if (before_enum is None and "enum" in before) or (
            after_enum is None and "enum" in after
        ):
            self.changes.add("unknown", location, "enum changed")
        elif before_enum is None:
            self._restriction_added(location, "enum restriction")
        elif after_enum is None:
            self._restriction_removed(location, "enum restriction")
        else:
            self._classify_set(before_enum, after_enum, location, "enum values")

    def _compare_const(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        if not _field_changed(before, after, "const"):
            return
        if "const" not in before:
            self._restriction_added(location, "constant value")
        elif "const" not in after:
            self._restriction_removed(location, "constant value")
        else:
            self.changes.add("breaking", location, "constant value changed")

    def _compare_variants(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        keyword: str,
        location: str,
        visited: frozenset[str],
    ) -> None:
        if keyword not in before and keyword not in after:
            return
        if keyword == "oneOf":
            before_expanded = _expanded_variants(self.baseline, before.get(keyword))
            after_expanded = _expanded_variants(self.current, after.get(keyword))
            if before_expanded is None or after_expanded is None:
                changed = _field_changed(before, after, keyword)
            else:
                changed = before_expanded != after_expanded
            if changed:
                self.changes.add("unknown", location, "oneOf schema changed")
            return

        before_variants = _variant_map(before.get(keyword))
        after_variants = _variant_map(after.get(keyword))
        if before_variants is None or after_variants is None:
            self.changes.add("unknown", location, f"{keyword} schema changed")
            return
        before_keys = set(before_variants)
        after_keys = set(after_variants)
        for key in sorted(before_keys & after_keys):
            self.compare(
                before_variants[key],
                after_variants[key],
                location=f"{location} {keyword} alternative",
                visited=visited,
            )
        self._classify_set(before_keys, after_keys, location, f"{keyword} alternatives")

    def _compare_object(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
        visited: frozenset[str],
    ) -> None:
        before_raw = before.get("properties")
        after_raw = after.get("properties")
        if (
            "properties" not in before
            and "properties" not in after
            and "required" not in before
            and "required" not in after
        ):
            return
        if ("properties" in before and not isinstance(before_raw, Mapping)) or (
            "properties" in after and not isinstance(after_raw, Mapping)
        ):
            self.changes.add("unknown", location, "property declaration changed")
            return
        before_properties = _object(cast(object, before_raw))
        after_properties = _object(cast(object, after_raw))
        before_required: set[str] | None = (
            _required_set(before.get("required"))
            if "required" in before
            else set[str]()
        )
        after_required: set[str] | None = (
            _required_set(after.get("required")) if "required" in after else set[str]()
        )
        if before_required is None or after_required is None:
            self.changes.add(
                "unknown", location, "required property declaration changed"
            )
            before_required = before_required or set[str]()
            after_required = after_required or set[str]()

        before_names = set(before_properties)
        after_names = set(after_properties)
        for name in sorted(after_names - before_names):
            property_location = f"{location} property {name!r}"
            if self.direction == "output":
                self.changes.add("breaking", property_location, "property added")
            elif name in after_required:
                self.changes.add(
                    "breaking", property_location, "required property added"
                )
            else:
                self.changes.add("additive", property_location, "property added")
        for name in sorted(before_names - after_names):
            self.changes.add(
                "breaking", f"{location} property {name!r}", "property removed"
            )
        for name in sorted(before_required ^ after_required):
            # A property added or removed above already reports the requiredness
            # change. Names declared on both sides, or on neither side, still
            # need an explicit directional classification.
            if name in before_names ^ after_names:
                continue
            property_location = f"{location} property {name!r}"
            after_is_required = name in after_required
            if self.direction == "input":
                severity: ChangeSeverity = (
                    "breaking" if after_is_required else "additive"
                )
            else:
                severity = "additive" if after_is_required else "breaking"
            message = (
                "property became required"
                if after_is_required
                else "property became optional"
            )
            self.changes.add(severity, property_location, message)
        for name in sorted(before_names & after_names):
            property_location = f"{location} property {name!r}"
            self.compare(
                before_properties[name],
                after_properties[name],
                location=property_location,
                visited=visited,
            )

    def _compare_items(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
        visited: frozenset[str],
    ) -> None:
        if "items" not in before and "items" not in after:
            return
        if "items" not in before or "items" not in after:
            self.changes.add("unknown", location, "array item schema changed")
            return
        self.compare(
            before.get("items"),
            after.get("items"),
            location=f"{location} items",
            visited=visited,
        )

    def _compare_bounds(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        for keyword in _LOWER_BOUNDS:
            self._compare_bound(before, after, keyword, lower=True, location=location)
        for keyword in _UPPER_BOUNDS:
            self._compare_bound(before, after, keyword, lower=False, location=location)

    def _compare_bound(
        self,
        before: JsonObject,
        after: JsonObject,
        keyword: str,
        *,
        lower: bool,
        location: str,
    ) -> None:
        if not _field_changed(before, after, keyword):
            return
        if keyword not in before:
            tightened = True
        elif keyword not in after:
            tightened = False
        else:
            before_value = before.get(keyword)
            after_value = after.get(keyword)
            if not _numbers(before_value, after_value):
                self.changes.add("unknown", location, f"{keyword} constraint changed")
                return
            before_number = cast(int | float, before_value)
            after_number = cast(int | float, after_value)
            tightened = (
                after_number > before_number if lower else after_number < before_number
            )
        self._classify_restriction(tightened, location, f"{keyword} constraint")

    def _compare_opaque(
        self, before: JsonObject, after: JsonObject, *, location: str
    ) -> None:
        for keyword in _OPAQUE_RESTRICTIONS:
            if not _field_changed(before, after, keyword):
                continue
            if keyword not in before:
                self._restriction_added(location, f"{keyword} constraint")
            elif keyword not in after:
                self._restriction_removed(location, f"{keyword} constraint")
            else:
                self.changes.add("unknown", location, f"{keyword} constraint changed")

    def _compare_additional_properties(
        self,
        before: JsonObject,
        after: JsonObject,
        *,
        location: str,
        visited: frozenset[str],
    ) -> None:
        before_value = before.get("additionalProperties", True)
        after_value = after.get("additionalProperties", True)
        if isinstance(before_value, Mapping) and isinstance(after_value, Mapping):
            self.compare(
                cast(object, before_value),
                cast(object, after_value),
                location=f"{location} additional properties",
                visited=visited,
            )
            return
        if before_value == after_value:
            return
        if not isinstance(before_value, bool) or not isinstance(after_value, bool):
            self.changes.add("unknown", location, "additional property schema changed")
            return
        self._classify_restriction(
            before_value and not after_value,
            location,
            "additional property policy",
        )

    def _restriction_added(self, location: str, noun: str) -> None:
        self._classify_restriction(True, location, noun, action="added")

    def _restriction_removed(self, location: str, noun: str) -> None:
        self._classify_restriction(False, location, noun, action="removed")

    def _classify_restriction(
        self,
        tightened: bool,
        location: str,
        noun: str,
        *,
        action: str = "changed",
    ) -> None:
        if self.direction == "input":
            severity: ChangeSeverity = "breaking" if tightened else "additive"
        else:
            severity = "additive" if tightened else "breaking"
        self.changes.add(severity, location, f"{noun} {action}")

    def _classify_set(
        self, before: set[str], after: set[str], location: str, noun: str
    ) -> None:
        if before == after:
            return
        removed = before - after
        added = after - before
        if removed and added:
            self.changes.add("breaking", location, f"{noun} changed")
        elif self.direction == "input":
            self.changes.add(
                "breaking" if removed else "additive", location, f"{noun} changed"
            )
        else:
            self.changes.add(
                "breaking" if added else "additive", location, f"{noun} changed"
            )


def compare_schema(
    before: object,
    after: object,
    *,
    direction: Direction,
    baseline: JsonObject,
    current: JsonObject,
    location: str,
    changes: ChangeSink,
) -> None:
    """Compare a boundary schema, following local component references."""
    _Comparator(
        baseline=baseline,
        current=current,
        changes=changes,
        direction=direction,
    ).compare(before, after, location=location)


def _resolve_reference(document: JsonObject, reference: str) -> object | None:
    if not reference.startswith("#/"):
        return None
    value: object = document
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        mapping = _object(value)
        if part not in mapping:
            return None
        value = mapping[part]
    return value


def _required_set(value: object) -> set[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    values = cast(Sequence[object], value)
    if not all(isinstance(item, str) for item in values):
        return None
    return set(cast(Sequence[str], values))


def _string_set(value: object) -> set[str] | None:
    if isinstance(value, str):
        return {value}
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        return None
    values = cast(Sequence[object], value)
    if not all(isinstance(item, str) for item in values):
        return None
    return set(cast(Sequence[str], values))


def _canonical_set(value: object) -> set[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    return {
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in cast(Sequence[object], value)
    }


def _variant_map(value: object) -> dict[str, object] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    return {
        json.dumps(item, sort_keys=True, separators=(",", ":")): item
        for item in cast(Sequence[object], value)
    }


def _expanded_variants(document: JsonObject, value: object) -> tuple[str, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return None
    return tuple(
        sorted(
            json.dumps(
                _expanded_value(document, item),
                sort_keys=True,
                separators=(",", ":"),
            )
            for item in cast(Sequence[object], value)
        )
    )


def _expanded_value(
    document: JsonObject,
    value: object,
    *,
    visited: frozenset[str] = frozenset(),
) -> object:
    if isinstance(value, Mapping):
        mapping = _object(cast(object, value))
        reference = mapping.get("$ref")
        if isinstance(reference, str):
            siblings = {
                key: _expanded_value(document, item, visited=visited)
                for key, item in mapping.items()
                if key != "$ref"
            }
            if reference in visited:
                return ["reference", reference, "cycle", siblings]
            resolved = _resolve_reference(document, reference)
            return [
                "reference",
                reference,
                _expanded_value(
                    document,
                    resolved,
                    visited=visited | {reference},
                ),
                siblings,
            ]
        return {
            key: _expanded_value(document, item, visited=visited)
            for key, item in mapping.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [
            _expanded_value(document, item, visited=visited)
            for item in cast(Sequence[object], value)
        ]
    return value


def _object(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        return {}
    mapping = cast(Mapping[object, object], value)
    return {key: item for key, item in mapping.items() if isinstance(key, str)}


def _field_changed(before: JsonObject, after: JsonObject, key: str) -> bool:
    return (key in before) != (key in after) or before.get(key) != after.get(key)


def _numbers(before: object, after: object) -> bool:
    return (
        isinstance(before, int | float)
        and not isinstance(before, bool)
        and isinstance(after, int | float)
        and not isinstance(after, bool)
    )
