"""Declaration-driven source rendering for Tenchi generators."""

from __future__ import annotations

import builtins
import keyword
import sys
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast, get_args, get_origin

from pydantic import BaseModel

from .contracts import Contract
from .errors import ConfigurationError

GENERATED_INCOMPLETE_PRAGMA = "# tenchi: incomplete"

_BOUNDARY_ARGUMENTS = ("params", "query", "headers", "request")


class GenerationConfigurationError(ConfigurationError):
    """A generator limitation with safe corrective guidance for callers."""

    def __init__(self, message: str, *, safe_message: str) -> None:
        self.safe_message = safe_message
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ContractUseCasePlan:
    """Rendered files and follow-up details derived from one HTTP contract."""

    files: dict[str, str]
    signature: str
    needs_response_headers_projector: bool


def contract_use_case_plan(
    declared: Contract[Any, Any],
    *,
    feature: str,
    name: str,
    named_annotations: Mapping[int, tuple[object, str, str]] | None = None,
) -> ContractUseCasePlan:
    """Render an incomplete, exactly typed use case from *declared*."""
    if declared.responses:
        raise GenerationConfigurationError(
            f"contract {declared.name!r} uses response definitions; their "
            "presenter input is application-owned, so Tenchi cannot infer the "
            "use-case return type",
            safe_message=(
                "The contract uses response definitions. Create this use case "
                "manually because its presenter input is application-owned."
            ),
        )

    renderer = _AnnotationRenderer(
        named_annotations or {},
        feature_module=f"app.features.{feature}",
        reserved_names=(name,),
    )
    context_annotation = renderer.add_import("app.server.context", "AppContext")
    parameters: list[tuple[str, str]] = []
    for parameter_name in _BOUNDARY_ARGUMENTS:
        annotation = getattr(declared, parameter_name)
        if annotation is not None:
            rendered_annotation = renderer.render(annotation, location=parameter_name)
            _check_annotation_line(
                rendered_annotation,
                location=parameter_name,
                prefix=f"    {parameter_name}: ",
                suffix=",",
            )
            parameters.append((parameter_name, rendered_annotation))
    parameters.append(("context", context_annotation))
    response_annotation = renderer.render(declared.response, location="response")
    _check_annotation_line(
        response_annotation,
        location="response",
        prefix=") -> ",
        suffix=":",
    )

    rendered_parameters = "\n".join(
        f"    {parameter_name}: {annotation},"
        for parameter_name, annotation in parameters
    )
    imports = renderer.render_imports()
    use_case = f"""\
from __future__ import annotations

{imports}


{GENERATED_INCOMPLETE_PRAGMA}
async def {name}(
{rendered_parameters}
) -> {response_annotation}:
    raise NotImplementedError("Implement {name}")
"""
    test = f"""\
import pytest

from ..use_cases.{name} import {name}


{GENERATED_INCOMPLETE_PRAGMA}
def test_{name}_requires_implementation() -> None:
    assert callable({name})
    pytest.fail("Implement {name} and replace this generated test")
"""
    _check_generated_line_lengths(use_case, label="use-case source")
    _check_generated_line_lengths(test, label="test source")
    signature_parameters = ", ".join(
        f"{parameter_name}: {annotation}" for parameter_name, annotation in parameters
    )
    return ContractUseCasePlan(
        files={
            f"use_cases/{name}.py": use_case,
            f"tests/test_{name}.py": test,
        },
        signature=(
            f"async def {name}({signature_parameters}) -> {response_annotation}"
        ),
        needs_response_headers_projector=declared.response_headers is not None,
    )


class _AnnotationRenderer:
    """Render importable runtime annotations as strict Python source."""

    def __init__(
        self,
        named_annotations: Mapping[int, tuple[object, str, str]],
        *,
        feature_module: str,
        reserved_names: tuple[str, ...],
    ) -> None:
        self._imports: dict[str, dict[str, str]] = {}
        self._generated_names = frozenset(reserved_names)
        self._local_names: dict[str, tuple[str, str]] = {
            name: ("<reserved>", name) for name in (*dir(builtins), *reserved_names)
        }
        self._named_annotations = named_annotations
        self._feature_module = feature_module

    def add_import(self, module: str, name: str) -> str:
        if (
            not _dotted_identifier(module)
            or not name.isidentifier()
            or keyword.iskeyword(name)
        ):
            raise GenerationConfigurationError(
                f"cannot render import {module}:{name}",
                safe_message=(
                    "Move the boundary type to a safely named definition in the "
                    "feature schemas module."
                ),
            )
        existing = self._imports.setdefault(module, {}).get(name)
        if existing is not None:
            return existing

        local_name = name
        owner = self._local_names.get(local_name)
        if owner is not None and owner != (module, name):
            suffix = 2
            local_name = f"_{name}_{suffix}"
            while local_name in self._local_names:
                suffix += 1
                local_name = f"_{name}_{suffix}"
        self._imports[module][name] = local_name
        self._local_names[local_name] = (module, name)
        return local_name

    def render(self, annotation: object, *, location: str) -> str:
        if annotation is None:
            return "None"
        if annotation is type(None):
            return self.add_import("types", "NoneType")
        if annotation is Any:
            return self.add_import("typing", "Any")
        if annotation is Ellipsis:
            return "..."

        named = self._named_annotations.get(id(annotation))
        if named is not None and named[0] is annotation:
            return self.add_import(named[1], named[2])

        origin = get_origin(annotation)
        if origin in (types.UnionType, typing.Union):
            return " | ".join(
                self.render(argument, location=location)
                for argument in get_args(annotation)
            )
        if origin is typing.Literal:
            literal = self.add_import("typing", "Literal")
            values = ", ".join(
                self._render_literal(argument, location=location)
                for argument in get_args(annotation)
            )
            return f"{literal}[{values}]"
        if origin is typing.Annotated:
            raise GenerationConfigurationError(
                f"contract {location} type {annotation!r} uses an inline "
                "Annotated value that cannot be imported safely; declare a named "
                "type in the feature schemas module",
                safe_message=(
                    "Declare a named boundary type in the feature schemas module "
                    "and reference it from the contract."
                ),
            )
        if (
            getattr(origin, "__module__", None) == "collections.abc"
            and getattr(origin, "__qualname__", None) == "Callable"
        ):
            raise GenerationConfigurationError(
                f"contract {location} type {annotation!r} is callable-shaped and "
                "cannot be rendered as a boundary annotation",
                safe_message=(
                    "Callable-shaped boundary annotations are unsupported. "
                    "Use a serializable schema type."
                ),
            )
        if origin is not None:
            typing_name = getattr(annotation, "_name", None)
            if isinstance(typing_name, str):
                rendered_origin = self.add_import("typing", typing_name)
            else:
                rendered_origin = self.render(origin, location=location)
            if not hasattr(annotation, "__args__"):
                return rendered_origin
            arguments = get_args(annotation)
            if not arguments:
                return f"{rendered_origin}[()]"
            rendered_arguments = ", ".join(
                self.render(argument, location=location) for argument in arguments
            )
            return f"{rendered_origin}[{rendered_arguments}]"

        pydantic_generic = getattr(annotation, "__pydantic_generic_metadata__", None)
        if (
            isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
            and isinstance(pydantic_generic, dict)
        ):
            generic_metadata = cast(Mapping[str, object], pydantic_generic)
            generic_origin = generic_metadata.get("origin")
            raw_arguments = generic_metadata.get("args")
            if generic_origin is not None and isinstance(raw_arguments, tuple):
                generic_arguments = cast(tuple[object, ...], raw_arguments)
                rendered_origin = self.render(generic_origin, location=location)
                rendered_arguments = ", ".join(
                    self.render(argument, location=location)
                    for argument in generic_arguments
                )
                return f"{rendered_origin}[{rendered_arguments}]"

        annotation_value = cast(object, annotation)
        module = getattr(annotation_value, "__module__", None)
        qualified_name = getattr(annotation_value, "__qualname__", None)
        if not isinstance(qualified_name, str):
            qualified_name = getattr(annotation_value, "__name__", None)
        if not isinstance(module, str) or not isinstance(qualified_name, str):
            raise GenerationConfigurationError(
                f"contract {location} type {annotation!r} has no importable name",
                safe_message=(
                    "Move the boundary type to a named, importable definition in "
                    "the feature schemas module."
                ),
            )
        if "<locals>" in qualified_name:
            raise GenerationConfigurationError(
                f"contract {location} type {qualified_name!r} is local to a "
                "function; move it to the feature schemas module",
                safe_message=(
                    "Move the boundary type to a named, importable definition in "
                    "the feature schemas module."
                ),
            )
        if not _dotted_identifier(module) or not _dotted_identifier(qualified_name):
            raise GenerationConfigurationError(
                f"contract {location} type {annotation!r} has an unsafe import name",
                safe_message=(
                    "Move the boundary type to a safely named definition in the "
                    "feature schemas module."
                ),
            )
        if module == "builtins":
            if qualified_name in self._generated_names:
                return self.add_import(module, qualified_name)
            return qualified_name
        if module == "app" or module.startswith("app."):
            parts = module.split(".")
            allowed = (
                module == "app.shared"
                or module.startswith("app.shared.")
                or (
                    len(parts) >= 4
                    and parts[:2] == ["app", "features"]
                    and parts[3] in {"domain", "schemas"}
                )
            )
            if not allowed:
                raise GenerationConfigurationError(
                    f"contract {location} type {annotation!r} is declared in "
                    f"{module!r}; move boundary types to a feature schemas or "
                    "domain module, or to app.shared",
                    safe_message=(
                        "Move boundary types to a feature schemas or domain module, "
                        "or to app.shared."
                    ),
                )

        root_name, _, nested = qualified_name.partition(".")
        local_name = self.add_import(module, root_name)
        return f"{local_name}.{nested}" if nested else local_name

    def _render_literal(self, value: object, *, location: str) -> str:
        if isinstance(value, Enum):
            if not value.name.isidentifier() or keyword.iskeyword(value.name):
                raise GenerationConfigurationError(
                    f"contract {location} Literal enum member {value.name!r} "
                    "cannot be rendered as a Python type expression",
                    safe_message=(
                        "Use identifier-safe enum member names in boundary Literal "
                        "types."
                    ),
                )
            enum_type = self.render(type(value), location=location)
            return f"{enum_type}.{value.name}"
        if value is None or isinstance(value, str | bytes | int | bool):
            return repr(value)
        raise GenerationConfigurationError(
            f"contract {location} Literal value {value!r} cannot be rendered "
            "safely; use strings, bytes, integers, booleans, None, or enum members",
            safe_message=(
                "Use strings, bytes, integers, booleans, None, or enum members in "
                "boundary Literal types."
            ),
        )

    def render_imports(self) -> str:
        sections: dict[str, list[str]] = {
            "standard": [],
            "third_party": [],
            "application": [],
            "relative": [],
        }
        for module in sorted(self._imports):
            entries = self._imports[module]
            names = [
                name if local_name == name else f"{name} as {local_name}"
                for name, local_name in sorted(entries.items())
            ]
            is_relative = module.startswith(f"{self._feature_module}.")
            import_module = (
                f"..{module.removeprefix(f'{self._feature_module}.')}"
                if is_relative
                else module
            )
            one_line = f"from {import_module} import {', '.join(names)}"
            if len(one_line) <= 88:
                rendered = one_line
            else:
                body = "\n".join(f"    {entry}," for entry in names)
                rendered = f"from {import_module} import (\n{body}\n)"

            root_module = module.partition(".")[0]
            if is_relative:
                section = "relative"
            elif module == "typing" or root_module in sys.stdlib_module_names:
                section = "standard"
            elif root_module == "app":
                section = "application"
            else:
                section = "third_party"
            sections[section].append(rendered)

        return "\n\n".join(
            "\n".join(sections[name])
            for name in ("standard", "third_party", "application", "relative")
            if sections[name]
        )


def _dotted_identifier(value: str) -> bool:
    return all(
        part.isidentifier() and not keyword.iskeyword(part) for part in value.split(".")
    )


def _check_annotation_line(
    annotation: str, *, location: str, prefix: str, suffix: str
) -> None:
    if len(f"{prefix}{annotation}{suffix}") > 88:
        raise GenerationConfigurationError(
            f"contract {location} type renders beyond 88 columns; declare a "
            "named type alias in the feature schemas module",
            safe_message=(
                "Declare a short named boundary type alias in the feature schemas "
                "module and reference it from the contract."
            ),
        )


def _check_generated_line_lengths(source: str, *, label: str) -> None:
    if any(len(line) > 88 for line in source.splitlines()):
        raise GenerationConfigurationError(
            f"generated {label} would exceed 88 columns; use shorter names or "
            "declare a named type alias in the feature schemas module",
            safe_message=(
                "Use a shorter use-case name or declare short named boundary types "
                "in the feature schemas module."
            ),
        )


__all__ = [
    "GENERATED_INCOMPLETE_PRAGMA",
    "ContractUseCasePlan",
    "GenerationConfigurationError",
    "contract_use_case_plan",
]
