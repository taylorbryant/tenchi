"""Renderer-independent route loading and OpenAPI compatibility operations."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import Any, Literal, TypedDict, cast

from ._cli_operations import openapi_defaults
from ._schema_compatibility import ChangeSeverity
from .compatibility import (
    CompatibilityReport,
    CompatibilityStatus,
    analyze_openapi_compatibility,
)
from .errors import ConfigurationError
from .openapi import openapi_schema
from .routes import RouteGroup


class OperationError(RuntimeError):
    """A user-actionable failure from a renderer-independent operation."""


class OpenApiChangePayload(TypedDict):
    severity: ChangeSeverity
    location: str
    message: str


class OpenApiCountsPayload(TypedDict):
    breaking: int
    additive: int
    metadata: int
    unknown: int


class OpenApiDiffPayload(TypedDict):
    schema_version: Literal[1]
    root: str
    baseline: str
    status: CompatibilityStatus
    compatible: bool
    counts: OpenApiCountsPayload
    changes: list[OpenApiChangePayload]


@dataclass(frozen=True, slots=True)
class OpenApiDiffResult:
    """Versioned compatibility result shared by the CLI and MCP server."""

    root: str
    baseline: str
    report: CompatibilityReport
    schema_version: Literal[1] = 1

    def as_dict(self) -> OpenApiDiffPayload:
        return {
            "schema_version": self.schema_version,
            "root": self.root,
            "baseline": self.baseline,
            "status": self.report.status,
            "compatible": self.report.compatible,
            "counts": {
                "breaking": self.report.count("breaking"),
                "additive": self.report.count("additive"),
                "metadata": self.report.count("metadata"),
                "unknown": self.report.count("unknown"),
            },
            "changes": [
                {
                    "severity": change.severity,
                    "location": change.location,
                    "message": change.message,
                }
                for change in self.report.changes
            ],
        }


def load_route_group(root: Path, target: str) -> RouteGroup:
    """Import *target* from *root* and return its Tenchi route group."""
    resolved_root = root.resolve()
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise OperationError(f"expected module:attribute, got {target!r}")

    root_string = str(resolved_root)
    if root_string in sys.path:
        sys.path.remove(root_string)
    sys.path.insert(0, root_string)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # import-time application failures are user errors
        raise OperationError(f"could not import {module_name!r}: {exc}") from exc

    if not hasattr(module, attribute):
        raise OperationError(f"module {module_name!r} has no attribute {attribute!r}")
    group = getattr(module, attribute)
    if not isinstance(group, RouteGroup):
        raise OperationError(
            f"{target!r} is not a tenchi RouteGroup (got {type(group).__name__})"
        )
    return group


@contextmanager
def isolated_project_imports(
    root: Path, *, module_names: tuple[str, ...]
) -> Generator[None]:
    """Give a persistent adapter fresh project imports without leaking state."""
    resolved_root = root.resolve()
    top_level_packages = frozenset(
        name.partition(".")[0] for name in module_names if name
    )
    original_path = list(sys.path)
    original_pycache_prefix = sys.pycache_prefix
    original_dont_write_bytecode = sys.dont_write_bytecode
    with TemporaryDirectory(prefix="tenchi-import-cache-") as cache:
        preserved = _project_modules(resolved_root, top_level_packages)
        for name in preserved:
            sys.modules.pop(name, None)
        importlib.invalidate_caches()
        sys.pycache_prefix = cache
        sys.dont_write_bytecode = True
        try:
            yield
        finally:
            for name in _project_modules(resolved_root, top_level_packages):
                sys.modules.pop(name, None)
            sys.modules.update(preserved)
            sys.path[:] = original_path
            sys.pycache_prefix = original_pycache_prefix
            sys.dont_write_bytecode = original_dont_write_bytecode


def _project_modules(
    root: Path, top_level_packages: frozenset[str]
) -> dict[str, ModuleType]:
    """Return loaded application modules while excluding the active environment."""
    modules: dict[str, ModuleType] = {}
    environment_root = Path(sys.prefix).resolve()
    for name, module in tuple(sys.modules.items()):
        if any(
            name == package or name.startswith(f"{package}.")
            for package in top_level_packages
        ):
            modules[name] = module
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            continue
        try:
            resolved_file = Path(module_file).resolve()
        except OSError:
            continue
        try:
            resolved_file.relative_to(environment_root)
        except ValueError:
            pass
        else:
            continue
        try:
            resolved_file.relative_to(root)
        except ValueError:
            continue
        modules[name] = module
    return modules


def openapi_diff_result(
    root: Path,
    *,
    routes: str,
    snapshot: Path,
    ref: str | None,
    title: str | None = None,
    version: str | None = None,
    description: str | None = None,
    security_json: str | None = None,
) -> OpenApiDiffResult:
    """Generate OpenAPI and compare it with a file or Git-backed baseline."""
    resolved_root = root.resolve()
    title, version, description, security_json = openapi_defaults(
        resolved_root,
        routes=routes,
        title=title,
        version=version,
        description=description,
        security_json=security_json,
    )
    group = load_route_group(resolved_root, routes)
    security = parse_security_json(security_json)
    try:
        current = openapi_schema(
            group,
            title=title,
            version=version,
            description=description,
            security=security,
        )
    except ConfigurationError as exc:
        raise OperationError(str(exc)) from exc

    if ref is None:
        if snapshot.is_absolute():
            baseline_path = snapshot.resolve()
            try:
                baseline_path.relative_to(resolved_root)
            except ValueError as exc:
                raise OperationError(
                    "snapshot path must stay inside the application root"
                ) from exc
        else:
            baseline_path = project_path(resolved_root, str(snapshot))
        try:
            baseline_text = baseline_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise OperationError(
                f"could not read baseline {str(snapshot)!r}: {exc}"
            ) from exc
        baseline_label = str(snapshot)
    else:
        baseline_text, baseline_label = read_git_snapshot(
            resolved_root, ref=ref, snapshot=snapshot
        )
    return compare_openapi_baseline(
        resolved_root,
        baseline_text=baseline_text,
        baseline_label=baseline_label,
        current=current,
    )


def compare_openapi_baseline(
    root: Path,
    *,
    baseline_text: str,
    baseline_label: str,
    current: Mapping[str, object],
) -> OpenApiDiffResult:
    """Compare a generated document with a serialized baseline."""
    try:
        baseline: object = json.loads(baseline_text)
    except json.JSONDecodeError as exc:
        raise OperationError(
            f"baseline {baseline_label!r} is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    try:
        report = analyze_openapi_compatibility(baseline, current)
    except ValueError as exc:
        raise OperationError(
            f"could not compare baseline {baseline_label!r}: {exc}"
        ) from exc
    return OpenApiDiffResult(
        root=str(root.resolve()), baseline=baseline_label, report=report
    )


def read_git_snapshot(root: Path, *, ref: str, snapshot: Path) -> tuple[str, str]:
    """Read *snapshot* from *ref* in the Git repository containing *root*."""
    if not ref.strip() or ref.startswith("-") or any(char.isspace() for char in ref):
        raise OperationError("Git ref must be non-empty and contain no whitespace")
    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:
        raise OperationError("could not run git; install Git to compare a ref") from exc
    except (OSError, UnicodeError) as exc:
        raise OperationError(f"could not inspect the Git repository: {exc}") from exc
    if root_result.returncode != 0:
        reason = root_result.stderr.strip() or "not inside a Git repository"
        raise OperationError(f"could not inspect the Git repository: {reason}")

    git_root = Path(root_result.stdout.strip()).resolve()
    resolved_snapshot = (
        snapshot.resolve() if snapshot.is_absolute() else (root / snapshot).resolve()
    )
    try:
        relative_snapshot = resolved_snapshot.relative_to(git_root).as_posix()
    except ValueError as exc:
        raise OperationError(
            "snapshot must resolve inside the current Git repository"
        ) from exc

    try:
        ref_result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        raise OperationError(f"could not resolve Git ref {ref!r}: {exc}") from exc
    if ref_result.returncode != 0:
        reason = ref_result.stderr.strip() or "unknown ref"
        raise OperationError(f"could not resolve Git ref {ref!r}: {reason}")

    baseline_label = f"{ref}:{relative_snapshot}"
    try:
        show_result = subprocess.run(
            ["git", "show", f"{ref_result.stdout.strip()}:{relative_snapshot}"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        raise OperationError(
            f"could not read baseline {baseline_label!r}: {exc}"
        ) from exc
    if show_result.returncode != 0:
        reason = show_result.stderr.strip() or "snapshot not found"
        raise OperationError(f"could not read baseline {baseline_label!r}: {reason}")
    return show_result.stdout, baseline_label


def parse_security_json(
    security_json: str | None,
) -> Mapping[str, Mapping[str, Any]] | None:
    """Parse the literal security metadata used by check and MCP operations."""
    if security_json is None:
        return None
    try:
        parsed: object = json.loads(security_json)
    except json.JSONDecodeError as exc:
        raise OperationError(
            "security metadata must be valid JSON "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise OperationError("security metadata must be a JSON object")
    return cast(Mapping[str, Mapping[str, Any]], parsed)


def project_path(root: Path, value: str) -> Path:
    """Resolve a project-relative path and reject escapes from *root*."""
    if not value.strip():
        raise OperationError("snapshot path must not be empty")
    candidate = Path(value)
    if candidate.is_absolute():
        raise OperationError("snapshot path must be relative to the application root")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise OperationError(
            "snapshot path must stay inside the application root"
        ) from exc
    return resolved
