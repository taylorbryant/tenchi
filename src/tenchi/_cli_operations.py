"""Renderer-independent operations shared by Tenchi command adapters."""

from __future__ import annotations

import ast
import json
import keyword
import re
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from ._cli_results import DiagnosticResult, DoctorResult, MakeResult
from .doctor import run_doctor
from .scaffold import feature_files, use_case_files

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def valid_name(name: str) -> bool:
    """Return whether *name* can safely appear in generated Python code."""
    return bool(_NAME.match(name)) and not keyword.iskeyword(name)


def write_files(root: Path, files: Mapping[str, str]) -> None:
    """Write a mapping of project-relative paths beneath *root*."""
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def make_feature_result(root: Path, *, name: str, dry_run: bool) -> MakeResult:
    """Plan or create a feature without choosing a result renderer."""
    resolved_root = root.resolve()
    if not valid_name(name):
        return MakeResult(
            root=str(resolved_root),
            artifact="feature",
            name=name,
            feature=name,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=(
                f"tenchi make feature: {name!r} is not a valid feature name; "
                "use snake_case, such as 'notes'"
            ),
        )

    features_root = resolved_root / "app" / "features"
    if not features_root.is_dir():
        return MakeResult(
            root=str(resolved_root),
            artifact="feature",
            name=name,
            feature=name,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=(
                "tenchi make feature: app/features/ not found; "
                "run this from an application root"
            ),
        )

    feature_root = features_root / name
    if feature_root.exists():
        relative_feature_root = feature_root.relative_to(resolved_root)
        return MakeResult(
            root=str(resolved_root),
            artifact="feature",
            name=name,
            feature=name,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=f"tenchi make feature: {relative_feature_root} already exists",
        )

    files = feature_files(name)
    if not dry_run:
        write_error = _write_files_transactionally(feature_root, files)
        if write_error is not None:
            return MakeResult(
                root=str(resolved_root),
                artifact="feature",
                name=name,
                feature=name,
                dry_run=False,
                ok=False,
                files=(),
                next_steps=(),
                error=f"tenchi make feature: could not create files: {write_error}",
            )
    relative_root = feature_root.relative_to(resolved_root)
    return MakeResult(
        root=str(resolved_root),
        artifact="feature",
        name=name,
        feature=name,
        dry_run=dry_run,
        ok=True,
        files=tuple((relative_root / path).as_posix() for path in files),
        next_steps=(
            f"Declare models in {relative_root}/schemas.py",
            f"Declare ports in {relative_root}/ports.py",
            f"Declare contracts in {relative_root}/contracts.py",
            f"Generate use cases: tenchi make use-case {name} <use_case_name>",
            f"Compose app.features.{name}.routes in app/server/routes.py",
        ),
    )


def make_use_case_result(
    root: Path, *, feature: str, name: str, dry_run: bool
) -> MakeResult:
    """Plan or create a use case without choosing a result renderer."""
    resolved_root = root.resolve()
    if not valid_name(name):
        return MakeResult(
            root=str(resolved_root),
            artifact="use-case",
            name=name,
            feature=feature,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=(
                f"tenchi make use-case: {name!r} is not a valid use case name; "
                "use snake_case, such as 'create_note'"
            ),
        )
    if not valid_name(feature):
        return MakeResult(
            root=str(resolved_root),
            artifact="use-case",
            name=name,
            feature=feature,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=(
                f"tenchi make use-case: {feature!r} is not a valid feature name; "
                "use snake_case, such as 'notes'"
            ),
        )

    feature_root = resolved_root / "app" / "features" / feature
    if not feature_root.is_dir():
        relative_root = Path("app") / "features" / feature
        return MakeResult(
            root=str(resolved_root),
            artifact="use-case",
            name=name,
            feature=feature,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=(
                f"tenchi make use-case: {relative_root} not found; "
                f"create it first with: tenchi make feature {feature}"
            ),
        )

    files = use_case_files(feature, name)
    existing = [path for path in files if (feature_root / path).exists()]
    if existing:
        existing_path = (feature_root / existing[0]).relative_to(resolved_root)
        return MakeResult(
            root=str(resolved_root),
            artifact="use-case",
            name=name,
            feature=feature,
            dry_run=dry_run,
            ok=False,
            files=(),
            next_steps=(),
            error=f"tenchi make use-case: {existing_path} already exists",
        )

    if not dry_run:
        write_error = _write_files_transactionally(feature_root, files)
        if write_error is not None:
            return MakeResult(
                root=str(resolved_root),
                artifact="use-case",
                name=name,
                feature=feature,
                dry_run=False,
                ok=False,
                files=(),
                next_steps=(),
                error=f"tenchi make use-case: could not create files: {write_error}",
            )
    relative_root = feature_root.relative_to(resolved_root)
    return MakeResult(
        root=str(resolved_root),
        artifact="use-case",
        name=name,
        feature=feature,
        dry_run=dry_run,
        ok=True,
        files=tuple((relative_root / path).as_posix() for path in files),
        next_steps=(
            f"Implement {name} and its test",
            f"Bind it to a contract in {relative_root}/routes.py",
        ),
    )


def doctor_result(root: Path) -> DoctorResult:
    """Return versioned doctor diagnostics without choosing a renderer."""
    resolved_root = root.resolve()
    if not (resolved_root / "app").is_dir():
        diagnostic = DiagnosticResult(
            code="TENCHI_DOCTOR_APP_ROOT_NOT_FOUND",
            severity="error",
            message="app/ not found; run this from an application root",
            path="app",
        )
        return DoctorResult(
            root=str(resolved_root), ok=False, diagnostics=(diagnostic,)
        )

    diagnostics = tuple(
        DiagnosticResult(
            code=finding.code,
            severity="error",
            message=finding.message,
            path=finding.path,
            line=finding.line or None,
        )
        for finding in run_doctor(resolved_root)
    )
    return DoctorResult(
        root=str(resolved_root), ok=not diagnostics, diagnostics=diagnostics
    )


def _write_files_transactionally(root: Path, files: Mapping[str, str]) -> str | None:
    destination_directories = _destination_directories(root, files)
    existing_directories: set[Path] = set()
    created_files: list[Path] = []
    try:
        existing_directories = {
            directory for directory in destination_directories if directory.exists()
        }
        with TemporaryDirectory(prefix=".tenchi-", dir=root.parent) as staging_name:
            staging_root = Path(staging_name)
            write_files(staging_root, files)
            for relative_path in files:
                source = staging_root / relative_path
                destination = root / relative_path
                if destination.exists():
                    raise FileExistsError(f"{destination} already exists")
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.replace(destination)
                created_files.append(destination)
    except OSError as exc:
        rollback_errors = _rollback_created_paths(
            created_files,
            destination_directories=destination_directories,
            existing_directories=existing_directories,
        )
        if rollback_errors:
            return f"{exc}; rollback incomplete: {'; '.join(rollback_errors)}"
        return str(exc)
    return None


def _destination_directories(root: Path, files: Mapping[str, str]) -> set[Path]:
    directories: set[Path] = set()
    for relative_path in files:
        directory = (root / relative_path).parent
        while directory != root.parent:
            directories.add(directory)
            directory = directory.parent
    return directories


def _rollback_created_paths(
    created_files: list[Path],
    *,
    destination_directories: set[Path],
    existing_directories: set[Path],
) -> list[str]:
    errors: list[str] = []
    for path in reversed(created_files):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(str(exc))
    new_directories = destination_directories - existing_directories
    for directory in sorted(
        new_directories, key=lambda path: len(path.parts), reverse=True
    ):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(str(exc))
    return errors


def openapi_defaults(
    root: Path,
    *,
    routes: str,
    title: str | None,
    version: str | None,
    description: str | None,
    security_json: str | None,
) -> tuple[str, str, str | None, str | None]:
    """Resolve check metadata from literal route-module declarations.

    Reading source keeps ``tenchi check --json`` free from application import
    side effects before the individual validation steps begin.
    """
    resolved_root = root.resolve()
    module_name, _, _ = routes.partition(":")
    declarations = _literal_openapi_declarations(resolved_root, module_name)
    declared_title = declarations.get("OPENAPI_TITLE")
    declared_version = declarations.get("OPENAPI_VERSION")
    declared_description = declarations.get("OPENAPI_DESCRIPTION")
    declared_security = declarations.get("OPENAPI_SECURITY")
    if security_json is None and isinstance(declared_security, Mapping):
        security_mapping = cast(Mapping[str, object], declared_security)
        try:
            security_json = json.dumps(security_mapping, separators=(",", ":"))
        except (TypeError, ValueError):
            security_json = repr(security_mapping)
    return (
        title
        or (declared_title if isinstance(declared_title, str) else resolved_root.name),
        version or (declared_version if isinstance(declared_version, str) else "0.1.0"),
        description
        if description is not None
        else (declared_description if isinstance(declared_description, str) else None),
        security_json,
    )


def _literal_openapi_declarations(root: Path, module_name: str) -> dict[str, object]:
    module_path = root.joinpath(*module_name.split(".")).with_suffix(".py")
    if not module_name or not module_path.is_file():
        return {}
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return {}

    declarations: dict[str, object] = {}
    for statement in tree.body:
        name, value = _assigned_value(statement)
        if (
            name
            not in {
                "OPENAPI_TITLE",
                "OPENAPI_VERSION",
                "OPENAPI_DESCRIPTION",
                "OPENAPI_SECURITY",
            }
            or value is None
        ):
            continue
        try:
            declarations[name] = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            continue
    return declarations


def _assigned_value(statement: ast.stmt) -> tuple[str | None, ast.expr | None]:
    if (
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
    ):
        return statement.targets[0].id, statement.value
    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return statement.target.id, statement.value
    return None, None
