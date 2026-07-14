"""Guard the public API surface.

The snapshot in ``api_snapshot.txt`` is the list of promises Tenchi has
made to installed applications: every public module, name, signature,
field, and constant. An unintentional change fails here before it ships
in a release; an intentional change is made in the open by regenerating
the snapshot and reviewing the diff:

    TENCHI_UPDATE_API_SNAPSHOT=1 uv run pytest tests/test_api_snapshot.py
"""

import dataclasses
import importlib
import inspect
import os
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel

PUBLIC_MODULES = (
    "tenchi",
    "tenchi.client",
    "tenchi.contracts",
    "tenchi.errors",
    "tenchi.execution",
    "tenchi.health",
    "tenchi.openapi",
    "tenchi.pagination",
    "tenchi.routes",
    "tenchi.server",
    "tenchi.testing",
)

SNAPSHOT_PATH = Path(__file__).parent / "api_snapshot.txt"


def render_api() -> str:
    sections = [
        _render_module(importlib.import_module(name)) for name in PUBLIC_MODULES
    ]
    return "\n\n".join(sections) + "\n"


def _render_module(module: ModuleType) -> str:
    lines = [f"# {module.__name__}"]
    for name in sorted(_public_names(module)):
        lines.extend(_render_value(name, getattr(module, name), module.__name__))
    return "\n".join(lines)


def _public_names(module: ModuleType) -> list[str]:
    declared = getattr(module, "__all__", None)
    if declared is not None:
        return [name for name in declared if name != "__version__"]
    names: list[str] = []
    for name, value in vars(module).items():
        if name.startswith("_") or inspect.ismodule(value):
            continue
        if inspect.isclass(value) or inspect.isfunction(value):
            if value.__module__ == module.__name__:
                names.append(name)
        elif name[0].isupper():  # constants and type aliases
            if type(value).__module__ == "typing":
                continue  # TypeVar, Self, ...: generic machinery, not API
            names.append(name)
    return names


def _render_value(name: str, value: Any, module_name: str) -> list[str]:
    if inspect.isclass(value):
        if value.__module__ != module_name:  # a root-level re-export
            return [f"{name} (re-export of {value.__module__}.{value.__qualname__})"]
        return _render_class(value)
    if inspect.isfunction(value):
        if value.__module__ != module_name:
            return [f"{name} (re-export of {value.__module__}.{value.__qualname__})"]
        return [f"def {name}{_signature(value)}"]
    return [f"{name} = {value!r}"]


def _render_class(cls: type) -> list[str]:
    bases = ", ".join(base.__name__ for base in cls.__bases__ if base is not object)
    lines = [f"class {cls.__name__}({bases})" if bases else f"class {cls.__name__}"]
    members: dict[str, Any] = dict(vars(cls))

    if dataclasses.is_dataclass(cls):
        for field in dataclasses.fields(cls):
            default = ""
            if field.default is not dataclasses.MISSING:
                default = f" = {field.default!r}"
            elif field.default_factory is not dataclasses.MISSING:
                default = " = <factory>"
            lines.append(f"    {field.name}: {field.type}{default}")
    elif issubclass(cls, BaseModel):
        for field_name, info in cls.model_fields.items():
            default = "" if info.is_required() else f" = {info.default!r}"
            lines.append(f"    {field_name}: {_annotation(info.annotation)}{default}")

    for member_name, member in sorted(members.items()):
        if member_name.startswith("_") and member_name != "__init__":
            continue
        if inspect.isfunction(member):
            lines.append(f"    def {member_name}{_signature(member)}")
        elif isinstance(member, property) and member.fget is not None:
            lines.append(f"    property {member_name}{_signature(member.fget)}")
    return lines


def _signature(function: Any) -> str:
    return str(inspect.signature(function))


def _annotation(annotation: Any) -> str:
    if annotation is None:
        return "None"
    if inspect.isclass(annotation):
        return annotation.__name__
    return str(annotation).replace("typing.", "")


def test_public_api_matches_snapshot() -> None:
    rendered = render_api()

    if os.environ.get("TENCHI_UPDATE_API_SNAPSHOT"):
        SNAPSHOT_PATH.write_text(rendered)
        return

    assert SNAPSHOT_PATH.exists(), (
        "No API snapshot found. Generate one with "
        "TENCHI_UPDATE_API_SNAPSHOT=1 uv run pytest tests/test_api_snapshot.py"
    )
    snapshot = SNAPSHOT_PATH.read_text()
    assert rendered == snapshot, (
        "The public API surface changed. If intentional, regenerate with "
        "TENCHI_UPDATE_API_SNAPSHOT=1 uv run pytest tests/test_api_snapshot.py "
        "and review the diff; the changelog must describe the change."
    )
