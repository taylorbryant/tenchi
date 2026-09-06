"""Composition checks and serialized-schema validation for HTTP responses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.protocols import Validator
from pydantic import TypeAdapter
from referencing import Registry

from .errors import ConfigurationError


def response_schema_validator(
    adapter: TypeAdapter[Any] | None, *, label: str
) -> Validator | None:
    """Compile the same aliased serialization schema that OpenAPI publishes."""
    if adapter is None:
        return None
    try:
        schema = adapter.json_schema(mode="serialization", by_alias=True)
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(
            schema, format_checker=FormatChecker(), registry=Registry()
        )
    except Exception as exc:
        raise ConfigurationError(
            f"{label}: response JSON Schema is invalid: {exc}"
        ) from exc


def validate_response_aliases(adapter: TypeAdapter[Any], *, label: str) -> None:
    """Reject output field names that the same declared type cannot read.

    Inspect Pydantic's core field declarations rather than comparing JSON
    Schema property order. This retains field identity through nested models,
    dataclasses, typed dictionaries, unions, and recursive definitions. The
    schema is read only; application classes and validators are never patched.
    """
    _check_schema(adapter.core_schema, label=label, config={})


def _check_schema(value: object, *, label: str, config: Mapping[str, Any]) -> None:
    if isinstance(value, list | tuple):
        for item in cast(list[object] | tuple[object, ...], value):
            _check_schema(item, label=label, config=config)
        return
    if not isinstance(value, Mapping):
        return
    schema = cast(Mapping[str, Any], value)
    kind_value = schema.get("type")
    kind = kind_value if isinstance(kind_value, str) else None
    if kind in {"model", "dataclass", "typed-dict"}:
        config = schema.get("config") or {}
    fields = schema.get("fields")
    if kind in {"model-fields", "typed-dict"} and isinstance(fields, Mapping):
        for name, field in cast(Mapping[str, Mapping[str, Any]], fields).items():
            _check_field(name, field, label=label, config=config)
    elif kind == "dataclass-args" and isinstance(fields, list):
        for field in cast(list[Mapping[str, Any]], fields):
            _check_field(field["name"], field, label=label, config=config)
    for key, child in schema.items():
        if kind is None or key not in {"metadata", "serialization", "config"}:
            _check_schema(child, label=label, config=config)


def _check_field(
    name: str,
    field: Mapping[str, Any],
    *,
    label: str,
    config: Mapping[str, Any],
) -> None:
    if field.get("serialization_exclude"):
        return
    wire_name = field.get("serialization_alias") or name
    validation_alias = field.get("validation_alias")
    accepted = {name} if validation_alias is None else set[str]()
    if config.get("populate_by_name") or config.get("validate_by_name"):
        accepted.add(name)
    if config.get("validate_by_alias", True):
        if isinstance(validation_alias, str):
            accepted.add(validation_alias)
        elif isinstance(validation_alias, list):
            # AliasPath and AliasChoices are represented as one path or a
            # list of paths. Only a one-segment path reads a flat JSON key.
            paths = cast(list[Any], validation_alias)
            if paths and not isinstance(paths[0], list):
                paths = [paths]
            for path in paths:
                if len(path) == 1 and isinstance(path[0], str):
                    accepted.add(path[0])
    if wire_name not in accepted:
        raise ConfigurationError(
            f"{label}: response field {name!r} serializes as {wire_name!r}, "
            "which its validation aliases cannot read; use Field(alias=...) "
            "or include the wire name in validation_alias"
        )
