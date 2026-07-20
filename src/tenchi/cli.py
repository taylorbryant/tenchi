"""The ``tenchi`` command-line interface.

Commands are intentionally few and reliable:

- ``tenchi new <name>`` scaffolds a new application with the prescribed
  structure.
- ``tenchi make feature <name>`` generates a feature skeleton; ``tenchi
  make use-case <feature> <name>`` generates a use-case stub and test.
  Generators create files and print wiring instructions — they never edit
  existing modules, because dependency wiring stays explicit and app-owned.
- ``tenchi routes`` prints the application's bound route table.
- ``tenchi openapi`` prints, writes, checks, or compatibility-diffs the
  application's canonical OpenAPI document.
- ``tenchi doctor`` checks dependency direction and prescribed structure.
- ``tenchi check`` runs the complete application validation loop.
- ``tenchi dev`` serves the application with uvicorn and reload.

The ``routes``, ``openapi``, and ``dev`` commands rely on the structural
convention that ``app/server/routes.py`` exposes ``routes`` and
``app/server/asgi.py`` exposes ``app``; both can be overridden by flag.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ._checks import run_check
from ._cli_operations import (
    doctor_result,
    make_feature_result,
    make_use_case_result,
    openapi_defaults,
    valid_name,
    write_files,
)
from ._cli_results import CheckResult, MakeResult
from .compatibility import (
    analyze_openapi_compatibility,
    render_compatibility_report,
)
from .errors import ConfigurationError
from .openapi import openapi_schema
from .routes import RouteGroup
from .scaffold import app_files
from .snapshots import (
    describe_openapi_drift,
    openapi_snapshot_diff,
    render_openapi_snapshot,
)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return parsed


_DEFAULT_ROUTES = "app.server.routes:routes"
_DEFAULT_API_ROUTES = "app.server.routes:api_routes"
_DEFAULT_APP = "app.server.asgi:app"


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "new":
        return _new(args.name)
    if args.command == "make":
        if args.artifact == "feature":
            return _make_feature(args.name, dry_run=args.dry_run, as_json=args.json)
        return _make_use_case(
            args.feature,
            args.name,
            dry_run=args.dry_run,
            as_json=args.json,
        )
    if args.command == "routes":
        return _routes(args.target, as_json=args.json)
    if args.command == "openapi":
        return _openapi(
            args.target,
            args.title,
            args.version,
            description=args.description,
            security_json=args.security,
            write=args.write,
            check=args.check,
            diff=args.diff,
            diff_ref=args.diff_ref,
            snapshot=args.snapshot,
            diff_format=args.diff_format,
        )
    if args.command == "doctor":
        return _doctor(as_json=args.json)
    if args.command == "check":
        return _check(
            routes=args.routes,
            title=args.title,
            version=args.version,
            description=args.description,
            snapshot=args.snapshot,
            security_json=args.security,
            timeout_seconds=args.timeout,
            as_json=args.json,
        )
    return _dev(args.app, args.host, args.port, reload=not args.no_reload)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tenchi",
        description="Contract-first, Python-native application framework.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    new_parser = subparsers.add_parser("new", help="Create a new Tenchi application")
    new_parser.add_argument("name", help="Application directory name, in snake_case")

    make_parser = subparsers.add_parser(
        "make", help="Generate application code from conventions"
    )
    make_subparsers = make_parser.add_subparsers(dest="artifact", required=True)
    feature_parser = make_subparsers.add_parser(
        "feature", help="Generate a feature skeleton under app/features/"
    )
    feature_parser.add_argument("name", help="Feature name, in snake_case")
    use_case_parser = make_subparsers.add_parser(
        "use-case", help="Generate a use-case stub and test in a feature"
    )
    use_case_parser.add_argument("feature", help="Existing feature name")
    use_case_parser.add_argument("name", help="Use case name, in snake_case")
    for generator_parser in (feature_parser, use_case_parser):
        generator_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview files and follow-up steps without writing",
        )
        generator_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a versioned machine-readable result",
        )

    routes_parser = subparsers.add_parser(
        "routes", help="Print the application's bound routes"
    )
    routes_parser.add_argument(
        "--routes",
        dest="target",
        default=_DEFAULT_ROUTES,
        help="module:attribute of the RouteGroup (default: %(default)s)",
    )
    routes_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the route table as JSON (a machine-readable app map)",
    )

    openapi_parser = subparsers.add_parser(
        "openapi",
        help="Print, write, check, or diff the application's OpenAPI document",
    )
    openapi_parser.add_argument(
        "--routes",
        dest="target",
        default=_DEFAULT_ROUTES,
        help="module:attribute of the RouteGroup (default: %(default)s)",
    )
    openapi_parser.add_argument(
        "--title",
        default=None,
        help="API title (default: the current directory name)",
    )
    openapi_parser.add_argument(
        "--version", default="0.1.0", help="API version (default: %(default)s)"
    )
    openapi_parser.add_argument(
        "--description",
        default=None,
        help="API description",
    )
    openapi_parser.add_argument(
        "--security",
        default=None,
        metavar="JSON",
        help="Security schemes as a JSON object",
    )
    openapi_mode = openapi_parser.add_mutually_exclusive_group()
    openapi_mode.add_argument(
        "--write",
        "--output",
        "-o",
        dest="write",
        default=None,
        help="Write a canonical snapshot (aliases: --output, -o)",
    )
    openapi_mode.add_argument(
        "--check",
        default=None,
        help="Fail if this snapshot differs from the generated document",
    )
    openapi_mode.add_argument(
        "--diff",
        default=None,
        metavar="BASELINE",
        help=("Classify changes from a baseline; fail on breaking or unknown changes"),
    )
    openapi_mode.add_argument(
        "--diff-ref",
        default=None,
        metavar="REF",
        help="Classify changes from the snapshot committed at a Git ref",
    )
    openapi_parser.add_argument(
        "--snapshot",
        default=None,
        metavar="PATH",
        help="Snapshot path for --diff-ref (default: openapi.json)",
    )
    openapi_parser.add_argument(
        "--diff-format",
        choices=("text", "json"),
        default="text",
        help="Compatibility report format (default: %(default)s)",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check dependency direction and the prescribed structure",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit versioned diagnostics as JSON",
    )

    check_parser = subparsers.add_parser(
        "check", help="Run formatting, lint, types, tests, doctor, and OpenAPI checks"
    )
    check_parser.add_argument(
        "--routes",
        default=_DEFAULT_API_ROUTES,
        help="module:attribute of the API RouteGroup (default: %(default)s)",
    )
    check_parser.add_argument(
        "--title",
        default=None,
        help=(
            "OpenAPI title (default: literal OPENAPI_TITLE in the route module "
            "or the current directory name)"
        ),
    )
    check_parser.add_argument(
        "--version",
        default=None,
        help=(
            "OpenAPI version (default: literal OPENAPI_VERSION in the route module "
            "or 0.1.0)"
        ),
    )
    check_parser.add_argument(
        "--description",
        default=None,
        help=(
            "OpenAPI description (default: literal OPENAPI_DESCRIPTION in the "
            "route module)"
        ),
    )
    check_parser.add_argument(
        "--security",
        default=None,
        metavar="JSON",
        help="OpenAPI security schemes as a JSON object",
    )
    check_parser.add_argument(
        "--snapshot",
        default="openapi.json",
        help="OpenAPI snapshot to check (default: %(default)s)",
    )
    check_parser.add_argument(
        "--timeout",
        default=600.0,
        type=_positive_float,
        metavar="SECONDS",
        help="Per-step timeout (default: %(default)s)",
    )
    check_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a versioned result with bounded failure output",
    )

    dev_parser = subparsers.add_parser(
        "dev", help="Serve the application with uvicorn and reload"
    )
    dev_parser.add_argument(
        "--app",
        default=_DEFAULT_APP,
        help="module:attribute of the ASGI app (default: %(default)s)",
    )
    dev_parser.add_argument("--host", default="127.0.0.1")
    dev_parser.add_argument("--port", type=int, default=8000)
    dev_parser.add_argument(
        "--no-reload", action="store_true", help="Disable auto-reload"
    )

    return parser


def _new(name: str) -> int:
    if not valid_name(name):
        _fail(
            f"tenchi new: {name!r} is not a valid application name; "
            "use snake_case, such as 'my_app'"
        )
        return 1

    target = Path(name)
    if target.exists():
        _fail(f"tenchi new: {name}/ already exists")
        return 1

    write_files(target, app_files(name))

    print(f"Created {name}/")
    print()
    print("Next steps:")
    print(f"  cd {name}")
    print("  uv sync")
    print("  uv run tenchi check")
    print("  uv run tenchi dev")
    print("  Swagger UI: http://127.0.0.1:8000/docs")
    return 0


def _make_feature(name: str, *, dry_run: bool, as_json: bool) -> int:
    result = make_feature_result(Path.cwd(), name=name, dry_run=dry_run)
    return _render_make_result(result, as_json=as_json)


def _make_use_case(feature: str, name: str, *, dry_run: bool, as_json: bool) -> int:
    result = make_use_case_result(
        Path.cwd(), feature=feature, name=name, dry_run=dry_run
    )
    return _render_make_result(result, as_json=as_json)


def _render_make_result(result: MakeResult, *, as_json: bool) -> int:
    if as_json:
        _print_json(result.as_dict())
        return 0 if result.ok else 1
    if not result.ok:
        _fail(result.error or "tenchi make: generation failed")
        return 1

    if result.dry_run:
        for path in result.files:
            print(f"Would create {path}")
    elif result.artifact == "feature":
        print(f"Created app/features/{result.name}/")
    else:
        for path in result.files:
            print(f"Created {path}")
    print()
    print("Next steps:")
    for index, step in enumerate(result.next_steps, start=1):
        print(f"  {index}. {step}")
    return 0


def _doctor(*, as_json: bool) -> int:
    root = Path.cwd()
    result = doctor_result(root)
    if as_json:
        _print_json(result.as_dict())
        return 0 if result.ok else 1
    if (
        result.diagnostics
        and result.diagnostics[0].code == "TENCHI_DOCTOR_APP_ROOT_NOT_FOUND"
    ):
        _fail(f"tenchi doctor: {result.diagnostics[0].message}")
        return 1

    if result.ok:
        print("doctor: no problems found")
        return 0

    for diagnostic in result.diagnostics:
        location = (
            f"{diagnostic.path}:{diagnostic.line}"
            if diagnostic.line is not None
            else diagnostic.path
        )
        print(f"{location}  {diagnostic.message}")
    print()
    print(f"doctor: {len(result.diagnostics)} problem(s) found")
    return 1


def _check(
    *,
    routes: str,
    title: str | None,
    version: str | None,
    description: str | None,
    snapshot: str,
    security_json: str | None,
    timeout_seconds: float,
    as_json: bool,
) -> int:
    root = Path.cwd()
    title, version, description, security_json = openapi_defaults(
        root,
        routes=routes,
        title=title,
        version=version,
        description=description,
        security_json=security_json,
    )
    result = run_check(
        root,
        routes=routes,
        title=title,
        version=version,
        description=description,
        snapshot=snapshot,
        security_json=security_json,
        timeout_seconds=timeout_seconds,
    )
    if as_json:
        _print_json(result.as_dict())
    else:
        _render_check_result(result)
    return 0 if result.ok else 1


def _render_check_result(result: CheckResult) -> None:
    if result.error is not None:
        _fail(f"tenchi check: {result.error}")
        return

    for step in result.steps:
        print(f"[{step.status}] {step.name} ({step.duration_seconds:.2f}s)")
        if step.status == "passed":
            continue
        print(f"  command: {shlex.join(step.command)}")
        if step.stdout:
            marker = " (tail retained)" if step.stdout_truncated else ""
            print(f"  stdout{marker}:")
            print(step.stdout.rstrip())
        if step.stderr:
            marker = " (tail retained)" if step.stderr_truncated else ""
            print(f"  stderr{marker}:")
            print(step.stderr.rstrip())

    passed = sum(step.status == "passed" for step in result.steps)
    total = len(result.steps)
    summary = "passed" if result.ok else "failed"
    print()
    print(
        f"check: {summary} ({passed}/{total} steps passed in "
        f"{result.duration_seconds:.2f}s)"
    )


def _routes(target: str, *, as_json: bool = False) -> int:
    group = _load_route_group("tenchi routes", target)
    if group is None:
        return 1

    if as_json:
        print(json.dumps(route_map(group), indent=2))
        return 0
    for line in format_routes(group):
        print(line)
    return 0


def route_map(group: RouteGroup) -> list[dict[str, object]]:
    """The route table as data: one entry per bound route, stable keys."""
    entries: list[dict[str, object]] = []
    for item in group:
        declared = item.contract
        entries.append(
            {
                "method": declared.method,
                "path": declared.path,
                "status": declared.status if not declared.responses else None,
                "responses": [
                    {"status": definition.status} for definition in declared.responses
                ],
                "use_case": f"{item.use_case.__module__}.{item.use_case.__qualname__}",
                "errors": [
                    {"code": e.code, "status": e.status} for e in declared.errors
                ],
                "tags": list(declared.tags),
                "public": declared.public,
                "summary": declared.summary,
                "response_headers": (
                    getattr(declared.response_headers, "__name__", None)
                    if declared.response_headers is not None
                    else None
                ),
                "deprecated": (
                    declared.deprecated.isoformat()
                    if isinstance(declared.deprecated, datetime)
                    else declared.deprecated
                ),
                "sunset": (
                    declared.sunset.isoformat() if declared.sunset is not None else None
                ),
                "max_request_bytes": declared.max_request_bytes,
                "timeout": declared.timeout,
            }
        )
    return entries


def _openapi(
    target: str,
    title: str | None,
    version: str,
    *,
    description: str | None,
    security_json: str | None,
    write: str | None,
    check: str | None,
    diff: str | None,
    diff_ref: str | None,
    snapshot: str | None,
    diff_format: str,
) -> int:
    if diff is None and diff_ref is None and diff_format != "text":
        _fail("tenchi openapi: --diff-format requires --diff or --diff-ref")
        return 1
    if snapshot is not None and diff_ref is None:
        _fail("tenchi openapi: --snapshot requires --diff-ref")
        return 1

    group = _load_route_group("tenchi openapi", target)
    if group is None:
        return 1

    security: Mapping[str, Mapping[str, Any]] | None = None
    if security_json is not None:
        try:
            parsed_security: object = json.loads(security_json)
        except json.JSONDecodeError as exc:
            _fail(
                "tenchi openapi: --security must be valid JSON "
                f"(line {exc.lineno}, column {exc.colno})"
            )
            return 1
        if not isinstance(parsed_security, Mapping):
            _fail("tenchi openapi: --security must be a JSON object")
            return 1
        security = cast(Mapping[str, Mapping[str, Any]], parsed_security)

    try:
        document = openapi_schema(
            group,
            title=title or Path.cwd().name,
            version=version,
            description=description,
            security=security,
        )
    except ConfigurationError as exc:
        _fail(f"tenchi openapi: {exc}")
        return 1
    rendered = render_openapi_snapshot(document)
    if write is not None:
        try:
            Path(write).write_text(rendered, encoding="utf-8")
        except OSError as exc:
            _fail(f"tenchi openapi: could not write snapshot {write!r}: {exc}")
            return 1
        print(f"Wrote {write}")
        return 0
    if check is not None:
        return _check_openapi_snapshot(Path(check), rendered)
    if diff is not None:
        return _diff_openapi_snapshot(
            Path(diff),
            document,
            output_format=diff_format,
        )
    if diff_ref is not None:
        return _diff_openapi_ref(
            diff_ref,
            Path(snapshot or "openapi.json"),
            document,
            output_format=diff_format,
        )
    sys.stdout.write(rendered)
    return 0


def _check_openapi_snapshot(path: Path, rendered: str) -> int:
    try:
        expected = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi openapi: could not read snapshot {str(path)!r}: {exc}")
        _fail(
            f"Rerun the same command with --write {path} instead of --check "
            "to create it."
        )
        return 1

    if expected == rendered:
        print(f"OpenAPI snapshot matches {path}")
        return 0

    _fail(f"tenchi openapi: snapshot differs: {path}")
    try:
        stored_document: object = json.loads(expected)
    except json.JSONDecodeError as exc:
        _fail(
            "  - stored snapshot is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno})"
        )
    else:
        generated_document: object = json.loads(rendered)
        for change in describe_openapi_drift(stored_document, generated_document):
            _fail(f"  - {change}")

    diff = openapi_snapshot_diff(expected, rendered, snapshot_path=str(path))
    if diff:
        print(file=sys.stderr)
        print(diff, file=sys.stderr, end="" if diff.endswith("\n") else "\n")
    print(file=sys.stderr)
    _fail(
        f"Run the same command with --write {path} instead of --check "
        "to accept this change."
    )
    return 1


def _diff_openapi_snapshot(
    path: Path,
    current: Mapping[str, object],
    *,
    output_format: str,
) -> int:
    try:
        baseline_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi openapi: could not read baseline {str(path)!r}: {exc}")
        return 1
    return _compare_openapi_baseline(
        baseline_text,
        baseline_label=str(path),
        current=current,
        output_format=output_format,
    )


def _diff_openapi_ref(
    ref: str,
    snapshot: Path,
    current: Mapping[str, object],
    *,
    output_format: str,
) -> int:
    baseline = _read_git_snapshot(ref, snapshot)
    if baseline is None:
        return 1
    baseline_text, baseline_label = baseline
    return _compare_openapi_baseline(
        baseline_text,
        baseline_label=baseline_label,
        current=current,
        output_format=output_format,
    )


def _read_git_snapshot(ref: str, snapshot: Path) -> tuple[str, str] | None:
    if not ref.strip() or ref.startswith("-") or any(char.isspace() for char in ref):
        _fail("tenchi openapi: --diff-ref must be a non-empty Git ref")
        return None
    try:
        root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        _fail("tenchi openapi: could not run git; install Git to use --diff-ref")
        return None
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi openapi: could not inspect the Git repository: {exc}")
        return None
    if root_result.returncode != 0:
        reason = root_result.stderr.strip() or "not inside a Git repository"
        _fail(f"tenchi openapi: could not inspect the Git repository: {reason}")
        return None

    root = Path(root_result.stdout.strip()).resolve()
    resolved_snapshot = snapshot.resolve()
    try:
        relative_snapshot = resolved_snapshot.relative_to(root).as_posix()
    except ValueError:
        _fail(
            "tenchi openapi: --snapshot must resolve inside the current Git repository"
        )
        return None

    try:
        ref_result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi openapi: could not resolve Git ref {ref!r}: {exc}")
        return None
    if ref_result.returncode != 0:
        reason = ref_result.stderr.strip() or "unknown ref"
        _fail(f"tenchi openapi: could not resolve Git ref {ref!r}: {reason}")
        return None

    commit = ref_result.stdout.strip()
    baseline_label = f"{ref}:{relative_snapshot}"
    try:
        show_result = subprocess.run(
            ["git", "show", f"{commit}:{relative_snapshot}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi openapi: could not read baseline {baseline_label!r}: {exc}")
        return None
    if show_result.returncode != 0:
        reason = show_result.stderr.strip() or "snapshot not found"
        _fail(f"tenchi openapi: could not read baseline {baseline_label!r}: {reason}")
        return None
    return show_result.stdout, baseline_label


def _compare_openapi_baseline(
    baseline_text: str,
    *,
    baseline_label: str,
    current: Mapping[str, object],
    output_format: str,
) -> int:
    try:
        baseline: object = json.loads(baseline_text)
    except json.JSONDecodeError as exc:
        _fail(
            f"tenchi openapi: baseline {baseline_label!r} is not valid JSON "
            f"(line {exc.lineno}, column {exc.colno})"
        )
        return 1

    try:
        report = analyze_openapi_compatibility(baseline, current)
    except ValueError as exc:
        _fail(f"tenchi openapi: could not compare baseline {baseline_label!r}: {exc}")
        return 1

    if output_format == "json":
        payload: dict[str, object] = {
            "baseline": baseline_label,
            **report.as_dict(),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(
            render_compatibility_report(report, baseline_path=baseline_label)
        )
    return 0 if report.compatible else 1


def _dev(app_target: str, host: str, port: int, *, reload: bool) -> int:
    try:
        import uvicorn
    except ImportError:
        _fail("tenchi dev: uvicorn is not installed; add it with: uv add --dev uvicorn")
        return 1

    print(f"Serving {app_target} on http://{host}:{port}")
    uvicorn.run(app_target, host=host, port=port, reload=reload, app_dir=".")
    return 0


def _load_route_group(command: str, target: str) -> RouteGroup | None:
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        _fail(f"{command}: expected module:attribute, got {target!r}")
        return None

    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # import-time app failures are user errors
        _fail(f"{command}: could not import {module_name!r}: {exc}")
        return None

    if not hasattr(module, attribute):
        _fail(f"{command}: module {module_name!r} has no attribute {attribute!r}")
        return None
    group = getattr(module, attribute)
    if not isinstance(group, RouteGroup):
        _fail(
            f"{command}: {target!r} is not a tenchi RouteGroup "
            f"(got {type(group).__name__})"
        )
        return None
    return group


def format_routes(group: RouteGroup) -> list[str]:
    """Format a route group as aligned ``METHOD PATH STATUS use_case`` rows."""
    rows: list[tuple[str, str, str, str]] = []
    for item in group:
        contract = item.contract
        use_case = f"{item.use_case.__module__}.{item.use_case.__qualname__}"
        if contract.errors:
            codes = ", ".join(d.code for d in contract.errors)
            use_case = f"{use_case}  [{codes}]"
        statuses = (
            ",".join(str(definition.status) for definition in contract.responses)
            if contract.responses
            else str(contract.status)
        )
        rows.append((contract.method, contract.path, statuses, use_case))

    if not rows:
        return ["no routes bound"]

    method_width = max(len(row[0]) for row in rows)
    path_width = max(len(row[1]) for row in rows)
    return [
        f"{method:<{method_width}}  {path:<{path_width}}  {status}  {use_case}"
        for method, path, status, use_case in rows
    ]


def _print_json(value: Mapping[str, object]) -> None:
    print(json.dumps(value, indent=2))


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
