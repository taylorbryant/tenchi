"""The ``tenchi`` command-line interface.

Commands are intentionally few and reliable:

- ``tenchi new <name>`` scaffolds a new application with the prescribed
  structure.
- ``tenchi make feature <name>`` generates a feature skeleton; ``tenchi
  make use-case <feature> <name>`` generates a use-case stub and test.
  Generators create files and print wiring instructions — they never edit
  existing modules, because dependency wiring stays explicit and app-owned.
- ``tenchi routes`` prints the application's bound route table.
- ``tenchi tools`` prints, writes, checks, or compatibility-diffs the
  application's registered tool manifest.
- ``tenchi map`` builds a source-backed graph of the application.
- ``tenchi openapi`` prints, writes, checks, or compatibility-diffs the
  application's canonical OpenAPI document.
- ``tenchi doctor`` checks dependency direction and prescribed structure.
- ``tenchi check`` runs the complete application validation loop.
- ``tenchi preflight`` observes the target deployment environment.
- ``tenchi task`` discovers and runs validated operational tasks.
- ``tenchi mcp`` serves the same structured operations to MCP-aware agents.
- ``tenchi dev`` serves the application with uvicorn and reload.

The ``routes``, ``tools``, ``map``, ``openapi``, ``check``, ``preflight``,
``task``, ``mcp``, and ``dev`` commands rely on the structural convention that
``app/server/routes.py`` exposes ``routes`` and ``api_routes``,
``app/server/tools.py`` exposes ``tools``,
``app/server/preflight.py`` exposes ``checks``, ``app/server/tasks.py`` exposes
``runner``, ``app/server/jobs.py`` exposes ``jobs``, and
``app/server/asgi.py`` exposes ``app``; targets can be overridden by flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shlex
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, cast

from ._agent_protocol import AgentResultName, validate_agent_result
from ._app_map import (
    AppMapNodeKind,
    app_map_node_kinds,
    format_app_map,
    map_app,
    project_app_map,
)
from ._checks import run_check
from ._cli_operations import (
    doctor_result,
    make_feature_result,
    make_use_case_result,
    openapi_defaults,
    routes_result,
    valid_name,
    write_files,
)
from ._cli_results import CheckResult, MakeResult
from ._job_operations import load_job_group
from ._openapi_operations import (
    OperationError,
    compare_openapi_baseline,
    load_route_group,
    read_git_snapshot,
)
from ._preflight_operations import (
    discard_preflight_output,
    load_preflight_group,
    preflight_result,
)
from ._task_operations import load_task_runner, task_list_result, task_run_result
from ._tool_operations import (
    compare_tool_baseline,
    load_tool_group,
    tool_list_result,
)
from .compatibility import (
    render_compatibility_report,
    render_tool_compatibility_report,
)
from .errors import ConfigurationError
from .openapi import openapi_schema
from .routes import RouteGroup
from .scaffold import app_files
from .snapshots import (
    describe_openapi_drift,
    openapi_snapshot_diff,
    render_openapi_snapshot,
    render_tool_snapshot,
    tool_snapshot_diff,
)
from .tools import ToolManifest, tool_manifest


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return parsed


def _app_map_kind_list(value: str) -> tuple[AppMapNodeKind, ...]:
    raw_kinds = tuple(dict.fromkeys(item.strip() for item in value.split(",")))
    invalid = [item for item in raw_kinds if item not in app_map_node_kinds]
    if not raw_kinds or "" in raw_kinds or invalid:
        choices = ", ".join(app_map_node_kinds)
        received = invalid[0] if invalid else value
        raise argparse.ArgumentTypeError(
            f"unknown app-map node kind {received!r}; choose from: {choices}"
        )
    return tuple(cast(AppMapNodeKind, item) for item in raw_kinds)


_DEFAULT_ROUTES = "app.server.routes:routes"
_DEFAULT_API_ROUTES = "app.server.routes:api_routes"
_DEFAULT_APP = "app.server.asgi:app"
_DEFAULT_PREFLIGHT = "app.server.preflight:checks"
_DEFAULT_TASKS = "app.server.tasks:runner"
_DEFAULT_JOBS = "app.server.jobs:jobs"
_DEFAULT_TOOLS = "app.server.tools:tools"


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
    if args.command == "tools":
        return _tools(
            args.target,
            as_json=args.json,
            write=args.write,
            check=args.check,
            diff=args.diff,
            diff_ref=args.diff_ref,
            snapshot=args.snapshot,
            diff_format=args.diff_format,
        )
    if args.command == "map":
        return _map_app(
            args.target,
            tasks_target=args.tasks,
            jobs_target=args.jobs,
            tools_target=args.tools,
            feature=args.feature,
            kinds=args.kinds,
            as_json=args.json,
        )
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
            tools=args.tools,
            tool_snapshot=args.tool_snapshot,
            security_json=args.security,
            timeout_seconds=args.timeout,
            as_json=args.json,
        )
    if args.command == "preflight":
        return _preflight(
            args.target,
            timeout=args.timeout,
            as_json=args.json,
        )
    if args.command == "task":
        if args.task_command == "list":
            return _task_list(args.target, as_json=args.json)
        return _task_run(
            args.target,
            args.name,
            input_json=args.input,
            as_json=args.json,
        )
    if args.command == "mcp":
        return _mcp(
            root=args.root,
            routes=args.routes,
            api_routes=args.api_routes,
            snapshot=args.snapshot,
            title=args.title,
            version=args.version,
            description=args.description,
            security_json=args.security,
            preflight=args.preflight,
            tasks=args.tasks,
            jobs=args.jobs,
            tools=args.tools,
            allow_task_runs=args.allow_task_runs,
            tool_snapshot=args.tool_snapshot,
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
        help="Emit the route table as JSON",
    )

    tools_parser = subparsers.add_parser(
        "tools",
        help="Print, write, check, or diff the registered tool manifest",
    )
    tools_parser.add_argument(
        "--tools",
        dest="target",
        default=_DEFAULT_TOOLS,
        help="module:attribute of the ToolGroup (default: %(default)s)",
    )
    tools_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a versioned machine-readable result",
    )
    tools_mode = tools_parser.add_mutually_exclusive_group()
    tools_mode.add_argument(
        "--write",
        default=None,
        metavar="PATH",
        help="Write the canonical tool snapshot",
    )
    tools_mode.add_argument(
        "--check",
        default=None,
        metavar="PATH",
        help="Fail if this snapshot differs from the generated manifest",
    )
    tools_mode.add_argument(
        "--diff",
        default=None,
        metavar="BASELINE",
        help="Classify changes from a baseline; fail on breaking or unknown changes",
    )
    tools_mode.add_argument(
        "--diff-ref",
        default=None,
        metavar="REF",
        help="Classify changes from the snapshot committed at a Git ref",
    )
    tools_parser.add_argument(
        "--snapshot",
        default=None,
        metavar="PATH",
        help="Snapshot path for --diff-ref (default: tools.json)",
    )
    tools_parser.add_argument(
        "--diff-format",
        choices=("text", "json"),
        default="text",
        help="Compatibility report format (default: %(default)s)",
    )

    map_parser = subparsers.add_parser(
        "map", help="Build a deterministic, source-backed application graph"
    )
    map_parser.add_argument(
        "--routes",
        dest="target",
        default=_DEFAULT_API_ROUTES,
        help="module:attribute of the API RouteGroup (default: %(default)s)",
    )
    map_parser.add_argument(
        "--tasks",
        default=_DEFAULT_TASKS,
        help="module:attribute of the TaskRunner (default: %(default)s)",
    )
    map_parser.add_argument(
        "--jobs",
        default=_DEFAULT_JOBS,
        help="module:attribute of the JobGroup (default: %(default)s)",
    )
    map_parser.add_argument(
        "--tools",
        default=_DEFAULT_TOOLS,
        help="module:attribute of the ToolGroup (default: %(default)s)",
    )
    map_parser.add_argument(
        "--feature",
        default=None,
        help="Project one feature and its directly related nodes",
    )
    map_parser.add_argument(
        "--kind",
        dest="kinds",
        default=None,
        type=_app_map_kind_list,
        metavar="KINDS",
        help="Comma-separated node kinds to include",
    )
    map_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the versioned application graph as JSON",
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
        "check",
        help=("Run formatting, lint, types, tests, doctor, OpenAPI, and tool checks"),
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
        "--tools",
        default=_DEFAULT_TOOLS,
        help="module:attribute of the ToolGroup (default: %(default)s)",
    )
    check_parser.add_argument(
        "--tool-snapshot",
        default="tools.json",
        help="Application-tool snapshot to check (default: %(default)s)",
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

    preflight_parser = subparsers.add_parser(
        "preflight",
        help="Run read-only checks against the target environment",
    )
    preflight_parser.add_argument(
        "--preflight",
        dest="target",
        default=_DEFAULT_PREFLIGHT,
        help="module:attribute of the PreflightGroup (default: %(default)s)",
    )
    preflight_parser.add_argument(
        "--timeout",
        default=None,
        type=_positive_float,
        metavar="SECONDS",
        help="Cap each check's declared timeout",
    )
    preflight_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a versioned, redacted result",
    )

    task_parser = subparsers.add_parser(
        "task", help="Discover and run validated operational tasks"
    )
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)
    task_list_parser = task_subparsers.add_parser(
        "list", help="List registered operational tasks and their contracts"
    )
    task_run_parser = task_subparsers.add_parser("run", help="Run one operational task")
    task_run_parser.add_argument("name", help="Stable dotted task name")
    task_run_parser.add_argument(
        "--input",
        default=None,
        metavar="JSON",
        help="Task input as JSON; omit for tasks without required input",
    )
    for operation_parser in (task_list_parser, task_run_parser):
        operation_parser.add_argument(
            "--tasks",
            dest="target",
            default=_DEFAULT_TASKS,
            help="module:attribute of the TaskRunner (default: %(default)s)",
        )
        operation_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a versioned machine-readable result",
        )

    mcp_parser = subparsers.add_parser(
        "mcp", help="Serve agent-readable Tenchi tools over MCP stdio"
    )
    mcp_parser.add_argument(
        "--root",
        default=".",
        help="Application root captured by every tool (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--routes",
        default=_DEFAULT_ROUTES,
        help="module:attribute used by the routes tool (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--api-routes",
        default=_DEFAULT_API_ROUTES,
        help=(
            "module:attribute used by app-map and OpenAPI tools (default: %(default)s)"
        ),
    )
    mcp_parser.add_argument(
        "--preflight",
        default=_DEFAULT_PREFLIGHT,
        help="module:attribute used by the preflight tool (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--tasks",
        default=_DEFAULT_TASKS,
        help="module:attribute used by task tools (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--jobs",
        default=_DEFAULT_JOBS,
        help="module:attribute used by the app-map tool (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--tools",
        default=_DEFAULT_TOOLS,
        help=(
            "module:attribute used by application-tool inspection "
            "(default: %(default)s)"
        ),
    )
    mcp_parser.add_argument(
        "--allow-task-runs",
        action="store_true",
        help="Expose the state-changing task_run tool (disabled by default)",
    )
    mcp_parser.add_argument(
        "--snapshot",
        default="openapi.json",
        help="Default project-relative OpenAPI baseline (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--tool-snapshot",
        default="tools.json",
        help="Default project-relative tool baseline (default: %(default)s)",
    )
    mcp_parser.add_argument(
        "--title",
        default=None,
        help="OpenAPI title override (default: discover from the route module)",
    )
    mcp_parser.add_argument(
        "--version",
        default=None,
        help="OpenAPI version override (default: discover from the route module)",
    )
    mcp_parser.add_argument(
        "--description",
        default=None,
        help="OpenAPI description override (default: discover from the route module)",
    )
    mcp_parser.add_argument(
        "--security",
        default=None,
        metavar="JSON",
        help="OpenAPI security schemes as a JSON object",
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
        _print_agent_json("make", result.as_dict())
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
        _print_agent_json("doctor", result.as_dict())
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
    tools: str,
    title: str | None,
    version: str | None,
    description: str | None,
    snapshot: str,
    tool_snapshot: str,
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
        tools=tools,
        tool_snapshot=tool_snapshot,
        security_json=security_json,
        timeout_seconds=timeout_seconds,
    )
    if as_json:
        _print_agent_json("check", result.as_dict())
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


def _preflight(
    target: str,
    *,
    timeout: float | None,
    as_json: bool,
) -> int:
    try:
        with discard_preflight_output():
            group = load_preflight_group(Path.cwd(), target)
            result = asyncio.run(
                preflight_result(
                    Path.cwd(),
                    target,
                    group,
                    timeout=timeout,
                )
            )
    except (OperationError, ConfigurationError) as exc:
        _fail(f"tenchi preflight: {exc}")
        return 1

    if as_json:
        _print_agent_json("preflight", result.as_dict())
    else:
        for check in result.checks:
            description = f"  {check.description}" if check.description else ""
            failure = (
                f"  [{check.failure_code}]" if check.failure_code is not None else ""
            )
            print(
                f"[{check.status}] {check.name} "
                f"({check.duration_seconds:.2f}s){failure}{description}"
            )
        summary = "passed" if result.ok else "failed"
        passed = sum(check.status == "passed" for check in result.checks)
        print()
        print(
            f"preflight: {summary} ({passed}/{len(result.checks)} checks passed "
            f"in {result.duration_seconds:.2f}s)"
        )
    return 0 if result.ok else 1


def _routes(target: str, *, as_json: bool = False) -> int:
    group = _load_route_group("tenchi routes", target)
    if group is None:
        return 1

    result = routes_result(Path.cwd(), group)
    if as_json:
        _print_agent_json("routes", result.as_dict())
        return 0
    for line in format_routes(group):
        print(line)
    return 0


def _map_app(
    target: str,
    *,
    tasks_target: str,
    jobs_target: str,
    tools_target: str,
    feature: str | None,
    kinds: Sequence[AppMapNodeKind] | None,
    as_json: bool,
) -> int:
    group = _load_route_group("tenchi map", target)
    if group is None:
        return 1
    try:
        runner = load_task_runner(Path.cwd(), tasks_target)
        jobs = load_job_group(Path.cwd(), jobs_target)
        tools = load_tool_group(Path.cwd(), tools_target)
    except OperationError as exc:
        _fail(f"tenchi map: {exc}")
        return 1

    result = map_app(Path.cwd(), group, runner.tasks, jobs, tools)
    if feature is not None:
        features = sorted(node.name for node in result.nodes if node.kind == "feature")
        if feature not in features:
            available = ", ".join(features) if features else "none"
            _fail(
                f"tenchi map: unknown feature {feature!r}; "
                f"available features: {available}"
            )
            return 1
    result = project_app_map(result, feature=feature, kinds=kinds)
    if as_json:
        _print_agent_json("app_map", result.as_dict())
    else:
        print(format_app_map(result))
    return 0


def route_map(group: RouteGroup) -> list[dict[str, object]]:
    """The route table as data: one entry per bound route, stable keys."""
    result = routes_result(Path.cwd(), group)
    return [dict(entry) for entry in result.as_dict()["routes"]]


def _task_list(target: str, *, as_json: bool) -> int:
    try:
        with redirect_stdout(sys.stderr if as_json else sys.stdout):
            runner = load_task_runner(Path.cwd(), target)
            result = task_list_result(Path.cwd(), target, runner)
    except OperationError as exc:
        _fail(f"tenchi task list: {exc}")
        return 1
    if as_json:
        _print_agent_json("task_list", result.as_dict())
        return 0
    if not result.tasks:
        print("No operational tasks are registered.")
        return 0
    width = max(len(item.name) for item in result.tasks)
    for item in result.tasks:
        input_label = (
            "required"
            if item.input_required
            else "optional"
            if item.input_schema is not None
            else "none"
        )
        description = f"  {item.description}" if item.description else ""
        print(f"{item.name:<{width}}  input: {input_label}{description}")
    return 0


def _task_run(
    target: str,
    name: str,
    *,
    input_json: str | None,
    as_json: bool,
) -> int:
    try:
        # Application code owns ordinary stdout. Keep structured CLI stdout
        # exclusively machine-readable by moving task prints to stderr.
        with redirect_stdout(sys.stderr if as_json else sys.stdout):
            runner = load_task_runner(Path.cwd(), target)
            result = asyncio.run(
                task_run_result(
                    Path.cwd(),
                    target,
                    runner,
                    name=name,
                    input_json=input_json,
                )
            )
    except OperationError as exc:
        _fail(f"tenchi task run: {exc}")
        return 1
    if as_json:
        _print_agent_json("task_run", result.as_dict())
    elif result.ok:
        print(f"Task {name!r} completed in {result.duration_seconds:.2f}s")
        if result.output is not None:
            json.dump(result.output, sys.stdout, indent=2, sort_keys=True)
            print()
    else:
        assert result.error is not None
        _fail(f"tenchi task run: [{result.error.code}] {result.error.message}")
        if result.error.details is not None:
            json.dump(result.error.details, sys.stderr, indent=2, sort_keys=True)
            print(file=sys.stderr)
    return 0 if result.ok else 1


def _tools(
    target: str,
    *,
    as_json: bool,
    write: str | None,
    check: str | None,
    diff: str | None,
    diff_ref: str | None,
    snapshot: str | None,
    diff_format: str,
) -> int:
    has_snapshot_mode = any(
        value is not None for value in (write, check, diff, diff_ref)
    )
    if as_json and has_snapshot_mode:
        _fail(
            "tenchi tools: --json cannot be combined with snapshot write, "
            "check, or diff modes"
        )
        return 1
    if diff is None and diff_ref is None and diff_format != "text":
        _fail("tenchi tools: --diff-format requires --diff or --diff-ref")
        return 1
    if snapshot is not None and diff_ref is None:
        _fail("tenchi tools: --snapshot requires --diff-ref")
        return 1

    try:
        with redirect_stdout(sys.stderr):
            group = load_tool_group(Path.cwd(), target)
            manifest = tool_manifest(group)
    except OperationError as exc:
        _fail(f"tenchi tools: {exc}")
        return 1

    rendered = render_tool_snapshot(manifest)
    if write is not None:
        try:
            Path(write).write_text(rendered, encoding="utf-8")
        except OSError as exc:
            _fail(f"tenchi tools: could not write snapshot {write!r}: {exc}")
            return 1
        print(f"Wrote {write}")
        return 0
    if check is not None:
        return _check_tool_snapshot(Path(check), rendered, manifest)
    if diff is not None:
        return _diff_tool_snapshot(
            Path(diff),
            manifest,
            output_format=diff_format,
        )
    if diff_ref is not None:
        return _diff_tool_ref(
            diff_ref,
            Path(snapshot or "tools.json"),
            manifest,
            output_format=diff_format,
        )
    if as_json:
        _print_agent_json(
            "tool_list",
            tool_list_result(Path.cwd(), group).as_dict(),
        )
    else:
        sys.stdout.write(rendered)
    return 0


def _check_tool_snapshot(
    path: Path,
    rendered: str,
    current: ToolManifest,
) -> int:
    try:
        expected = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi tools: could not read snapshot {str(path)!r}: {exc}")
        _fail(
            f"Rerun the same command with --write {path} instead of --check "
            "to create it."
        )
        return 1

    if expected == rendered:
        print(f"Tool snapshot matches {path}")
        return 0

    _fail(f"tenchi tools: snapshot differs: {path}")
    try:
        result = compare_tool_baseline(
            Path.cwd(),
            baseline_text=expected,
            baseline_label=str(path),
            current=current,
        )
    except OperationError as exc:
        _fail(f"  - {exc}")
    else:
        for change in result.report.changes:
            _fail(f"  - [{change.severity}] {change.location}: {change.message}")

    diff = tool_snapshot_diff(expected, rendered, snapshot_path=str(path))
    if diff:
        print(file=sys.stderr)
        print(diff, file=sys.stderr, end="" if diff.endswith("\n") else "\n")
    print(file=sys.stderr)
    _fail(
        f"Run the same command with --write {path} instead of --check "
        "to accept this change."
    )
    return 1


def _diff_tool_snapshot(
    path: Path,
    current: ToolManifest,
    *,
    output_format: str,
) -> int:
    try:
        baseline_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi tools: could not read baseline {str(path)!r}: {exc}")
        return 1
    return _compare_tool_baseline(
        baseline_text,
        baseline_label=str(path),
        current=current,
        output_format=output_format,
    )


def _diff_tool_ref(
    ref: str,
    snapshot: Path,
    current: ToolManifest,
    *,
    output_format: str,
) -> int:
    try:
        baseline_text, baseline_label = read_git_snapshot(
            Path.cwd(),
            ref=ref,
            snapshot=snapshot,
        )
    except OperationError as exc:
        _fail(f"tenchi tools: {exc}")
        return 1
    return _compare_tool_baseline(
        baseline_text,
        baseline_label=baseline_label,
        current=current,
        output_format=output_format,
    )


def _compare_tool_baseline(
    baseline_text: str,
    *,
    baseline_label: str,
    current: ToolManifest,
    output_format: str,
) -> int:
    try:
        result = compare_tool_baseline(
            Path.cwd(),
            baseline_text=baseline_text,
            baseline_label=baseline_label,
            current=current,
        )
    except OperationError as exc:
        _fail(f"tenchi tools: {exc}")
        return 1

    if output_format == "json":
        _print_agent_json("tool_diff", result.as_dict())
    else:
        sys.stdout.write(
            render_tool_compatibility_report(
                result.report,
                baseline_path=baseline_label,
            )
        )
    return 0 if result.report.compatible else 1


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
    try:
        baseline_text, baseline_label = read_git_snapshot(
            Path.cwd(), ref=ref, snapshot=snapshot
        )
    except OperationError as exc:
        _fail(f"tenchi openapi: {exc}")
        return 1
    return _compare_openapi_baseline(
        baseline_text,
        baseline_label=baseline_label,
        current=current,
        output_format=output_format,
    )


def _compare_openapi_baseline(
    baseline_text: str,
    *,
    baseline_label: str,
    current: Mapping[str, object],
    output_format: str,
) -> int:
    try:
        result = compare_openapi_baseline(
            Path.cwd(),
            baseline_text=baseline_text,
            baseline_label=baseline_label,
            current=current,
        )
    except OperationError as exc:
        _fail(f"tenchi openapi: {exc}")
        return 1

    if output_format == "json":
        _print_agent_json("openapi_diff", result.as_dict())
    else:
        sys.stdout.write(
            render_compatibility_report(result.report, baseline_path=baseline_label)
        )
    return 0 if result.report.compatible else 1


def _mcp(
    *,
    root: str,
    routes: str,
    api_routes: str,
    preflight: str,
    tasks: str,
    jobs: str,
    tools: str,
    allow_task_runs: bool,
    snapshot: str,
    tool_snapshot: str,
    title: str | None,
    version: str | None,
    description: str | None,
    security_json: str | None,
) -> int:
    try:
        from ._mcp_server import McpServerOptions, run_mcp_server
    except ImportError as exc:
        if exc.name == "mcp" or (exc.name is not None and exc.name.startswith("mcp.")):
            _fail(
                "tenchi mcp: MCP support is not installed; "
                'run: uv add --dev "tenchi[mcp]"'
            )
            return 1
        raise

    try:
        run_mcp_server(
            McpServerOptions(
                root=Path(root),
                routes=routes,
                api_routes=api_routes,
                preflight=preflight,
                tasks=tasks,
                jobs=jobs,
                tools=tools,
                allow_task_runs=allow_task_runs,
                snapshot=snapshot,
                tool_snapshot=tool_snapshot,
                title=title,
                version=version,
                description=description,
                security_json=security_json,
            )
        )
    except OperationError as exc:
        _fail(f"tenchi mcp: {exc}")
        return 1
    return 0


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
    try:
        return load_route_group(Path.cwd(), target)
    except OperationError as exc:
        _fail(f"{command}: {exc}")
        return None


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


def _print_agent_json(
    result_name: AgentResultName,
    value: Mapping[str, object],
) -> None:
    print(json.dumps(validate_agent_result(result_name, value), indent=2))


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
