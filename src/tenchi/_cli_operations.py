"""Renderer-independent operations shared by Tenchi command adapters."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import keyword
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from ._change_plans import (
    ChangePlan,
    create_contract_use_case_change_plan,
    render_change_plan,
)
from ._cli_results import (
    DiagnosticResult,
    DoctorResult,
    MakeResult,
    RouteEntryResult,
    RoutesResult,
)
from ._generation import (
    GenerationConfigurationError,
    contract_use_case_plan,
    named_contract_annotations,
)
from .contracts import Contract
from .doctor import run_doctor
from .openapi import openapi_schema
from .routes import RouteGroup, route, route_group
from .scaffold import feature_files, use_case_files

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ContractLoadError(RuntimeError):
    """The requested generator source could not be loaded as a contract."""


def valid_name(name: str) -> bool:
    """Return whether *name* can safely appear in generated Python code."""
    return bool(_NAME.match(name)) and not keyword.iskeyword(name)


def routes_result(root: Path, group: RouteGroup) -> RoutesResult:
    """Return the stable, versioned route table for *group*."""
    entries: list[RouteEntryResult] = []
    for item in group:
        declared = item.contract
        entries.append(
            RouteEntryResult(
                method=declared.method,
                path=declared.path,
                status=declared.status if not declared.responses else None,
                responses=tuple(definition.status for definition in declared.responses),
                use_case=(f"{item.use_case.__module__}.{item.use_case.__qualname__}"),
                errors=tuple((error.code, error.status) for error in declared.errors),
                tags=declared.tags,
                public=declared.public,
                summary=declared.summary,
                response_headers=(
                    getattr(declared.response_headers, "__name__", None)
                    if declared.response_headers is not None
                    else None
                ),
                deprecated=(
                    declared.deprecated.isoformat()
                    if isinstance(declared.deprecated, datetime)
                    else declared.deprecated
                ),
                sunset=(
                    declared.sunset.isoformat() if declared.sunset is not None else None
                ),
                max_request_bytes=declared.max_request_bytes,
                timeout=declared.timeout,
            )
        )
    return RoutesResult(root=str(root.resolve()), routes=tuple(entries))


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
            (
                "Derive a boundary signature with --from-contract "
                f"app.features.{name}.contracts:<contract_name>"
            ),
            f"Compose app.features.{name}.routes in app/server/routes.py",
            f"Compose app.features.{name}.jobs in app/server/jobs.py",
            f"Compose app.features.{name}.tasks in app/server/tasks.py",
            f"Compose app.features.{name}.tools in app/server/tools.py",
            (f"Compose app.features.{name}.evaluations in app/server/evaluations.py"),
        ),
    )


def make_use_case_result(
    root: Path,
    *,
    feature: str,
    name: str,
    dry_run: bool,
    from_contract: str | None = None,
    change_plan_path: str | None = None,
    change_plan_baseline_ref: str | None = None,
    change_plan_baseline_commit: str | None = None,
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

    output_paths = (
        f"use_cases/{name}.py",
        f"tests/test_{name}.py",
    )
    existing = [path for path in output_paths if (feature_root / path).exists()]
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

    plan = None
    change_plan: ChangePlan | None = None
    normalized_change_plan_path: str | None = None
    if change_plan_path is not None:
        try:
            normalized_change_plan_path = _project_relative_output_path(
                resolved_root,
                change_plan_path,
            )
        except ValueError as exc:
            return MakeResult(
                root=str(resolved_root),
                artifact="use-case",
                name=name,
                feature=feature,
                dry_run=dry_run,
                ok=False,
                files=(),
                next_steps=(),
                error=f"tenchi make use-case: {exc}",
            )
        if (resolved_root / normalized_change_plan_path).exists():
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
                    "tenchi make use-case: change plan "
                    f"{normalized_change_plan_path} already exists"
                ),
            )
    baseline_values = (
        change_plan_baseline_ref,
        change_plan_baseline_commit,
    )
    if (baseline_values[0] is None) != (baseline_values[1] is None):
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
                "tenchi make use-case: change-plan baseline ref and commit "
                "must be provided together"
            ),
        )
    if normalized_change_plan_path is not None and baseline_values[0] is None:
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
                "tenchi make use-case: a persisted change plan requires an "
                "immutable baseline"
            ),
        )
    if from_contract is not None:
        module_name, separator, attribute = from_contract.partition(":")
        expected_module = f"app.features.{feature}.contracts"
        if not separator or not module_name or not attribute:
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
                    "tenchi make use-case: --from-contract must be a "
                    "module:attribute target"
                ),
            )
        if module_name != expected_module:
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
                    "tenchi make use-case: --from-contract must target "
                    f"{expected_module}:<contract> for feature {feature!r}"
                ),
            )
        declared = load_contract(resolved_root, module_name, attribute)
        try:
            plan = contract_use_case_plan(
                declared,
                feature=feature,
                name=name,
                named_annotations=named_contract_annotations(resolved_root, feature),
            )
        except GenerationConfigurationError as exc:
            return MakeResult(
                root=str(resolved_root),
                artifact="use-case",
                name=name,
                feature=feature,
                dry_run=dry_run,
                ok=False,
                files=(),
                next_steps=(),
                error=f"tenchi make use-case: {exc.safe_message}",
            )
        _validate_contract_generation_source(declared)
        files = plan.files
        if change_plan_baseline_ref is not None:
            assert change_plan_baseline_commit is not None
            change_plan = create_contract_use_case_change_plan(
                baseline_ref=change_plan_baseline_ref,
                baseline_commit=change_plan_baseline_commit,
                feature=feature,
                contract_target=from_contract,
                contract_name=declared.name,
                contract_method=declared.method,
                contract_path=declared.path,
                use_case_name=name,
                use_case_signature=plan.signature,
            )
    else:
        if normalized_change_plan_path is not None or baseline_values[0] is not None:
            return MakeResult(
                root=str(resolved_root),
                artifact="use-case",
                name=name,
                feature=feature,
                dry_run=dry_run,
                ok=False,
                files=(),
                next_steps=(),
                error=("tenchi make use-case: change plans require --from-contract"),
            )
        files = use_case_files(feature, name)

    relative_root = feature_root.relative_to(resolved_root)
    writes: dict[str, str] = {}
    if not dry_run:
        writes.update(
            ((relative_root / relative_path).as_posix(), content)
            for relative_path, content in files.items()
        )
    if normalized_change_plan_path is not None and not dry_run:
        assert change_plan is not None
        if normalized_change_plan_path in writes:
            return MakeResult(
                root=str(resolved_root),
                artifact="use-case",
                name=name,
                feature=feature,
                dry_run=dry_run,
                ok=False,
                files=(),
                next_steps=(),
                error="tenchi make use-case: change plan path conflicts with output",
            )
        writes[normalized_change_plan_path] = render_change_plan(change_plan)

    if writes:
        write_error = _write_files_transactionally(resolved_root, writes)
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
    if plan is None:
        next_steps = (
            f"Implement {name} and its test",
            f"Bind it to a contract in {relative_root}/routes.py",
        )
    else:
        next_steps_list = [
            f"Implement {plan.signature}",
            (
                "Replace the failing generated test and remove "
                "'# tenchi: incomplete' from both generated files"
            ),
            (f"Bind {from_contract} to {name} in {relative_root}/routes.py"),
        ]
        if plan.needs_response_headers_projector:
            next_steps_list.append(
                "Add the contract's synchronous response_headers projector at "
                "the route boundary"
            )
        next_steps_list.append("Run tenchi check")
        if normalized_change_plan_path is not None:
            assert change_plan_baseline_ref is not None
            next_steps_list.append(
                "Run tenchi verify --base-ref "
                f"{change_plan_baseline_ref} --change-plan "
                f"{normalized_change_plan_path}"
            )
        next_steps = tuple(next_steps_list)
    return MakeResult(
        root=str(resolved_root),
        artifact="use-case",
        name=name,
        feature=feature,
        dry_run=dry_run,
        ok=True,
        files=tuple((relative_root / path).as_posix() for path in files),
        next_steps=next_steps,
        change_plan=change_plan,
        change_plan_path=normalized_change_plan_path,
    )


def _project_relative_output_path(root: Path, value: str) -> str:
    if not value.strip():
        raise ValueError("change plan path must not be empty")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError("change plan path must be relative to the application root")
    resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "change plan path must stay inside the application root"
        ) from exc
    if relative == Path("."):
        raise ValueError("change plan path must name a file")
    return relative.as_posix()


def load_contract(
    root: Path, module_name: str, attribute: str
) -> Contract[object, object]:
    original_path = sys.path.copy()
    root_string = str(root)
    if root_string in sys.path:
        sys.path.remove(root_string)
    sys.path.insert(0, root_string)
    try:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise ContractLoadError(f"could not import {module_name!r}: {exc}") from exc
        try:
            declared = getattr(module, attribute)
        except AttributeError as exc:
            raise ContractLoadError(
                f"module {module_name!r} has no attribute {attribute!r}"
            ) from exc
        except Exception as exc:
            raise ContractLoadError(
                f"could not load {module_name}:{attribute}: {exc}"
            ) from exc
        if not isinstance(declared, Contract):
            raise ContractLoadError(
                f"{module_name}:{attribute} is not a tenchi Contract "
                f"(got {type(declared).__name__})"
            )
        return cast(Contract[object, object], declared)
    finally:
        sys.path[:] = original_path


def _validate_contract_generation_source(
    declared: Contract[object, object],
) -> None:
    """Prove the declaration can be wired and described before generating."""

    async def generated_use_case() -> None:
        raise AssertionError("generation validation never invokes the use case")

    parameters = [
        inspect.Parameter(
            parameter_name,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=annotation,
        )
        for parameter_name in ("params", "query", "headers", "request")
        if (annotation := getattr(declared, parameter_name)) is not None
    ]
    parameters.append(
        inspect.Parameter(
            "context",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=object,
        )
    )
    cast(Any, generated_use_case).__signature__ = inspect.Signature(
        parameters,
        return_annotation=declared.response,
    )

    projector = None
    if declared.response_headers is not None:

        def response_headers_projector(_: object) -> object:
            raise AssertionError("generation validation never invokes the projector")

        cast(Any, response_headers_projector).__signature__ = inspect.Signature(
            [
                inspect.Parameter(
                    "result",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=declared.response,
                )
            ],
            return_annotation=declared.response_headers,
        )
        projector = response_headers_projector

    bound = route(declared, generated_use_case, response_headers=projector)
    openapi_schema(
        route_group(bound),
        title="Tenchi generation validation",
        version="0",
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
    """Resolve OpenAPI metadata from literal route-module declarations.

    Reading source keeps agent-facing commands free from application import
    side effects before their validation steps begin.
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
        if title is not None
        else (
            declared_title if isinstance(declared_title, str) else resolved_root.name
        ),
        version
        if version is not None
        else (declared_version if isinstance(declared_version, str) else "0.1.0"),
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
