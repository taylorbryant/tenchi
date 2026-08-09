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
- ``tenchi verify`` adds architecture and Git-baseline compatibility evidence.
- ``tenchi preflight`` observes the target deployment environment.
- ``tenchi eval`` snapshots, discovers, and runs application-owned AI evaluations.
- ``tenchi task`` discovers and runs validated operational tasks.
- ``tenchi mcp`` serves the same structured operations to MCP-aware agents.
- ``tenchi dev`` serves the application with uvicorn and reload.

The ``routes``, ``tools``, ``map``, ``openapi``, ``check``, ``verify``,
``preflight``, ``eval``, ``task``, ``mcp``, and ``dev`` commands rely on the
structural convention that ``app/server/routes.py`` exposes ``routes`` and
``api_routes``,
``app/server/tools.py`` exposes ``tools``,
``app/server/preflight.py`` exposes ``checks``, ``app/server/tasks.py`` exposes
``runner``, ``app/server/evaluations.py`` exposes ``runner``,
``app/server/jobs.py`` exposes ``jobs``, and
``app/server/asgi.py`` exposes ``app``; targets can be overridden by flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import shlex
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
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
from ._cli_results import (
    AgentOperationErrorCode,
    AgentOperationErrorDetails,
    AgentOperationErrorResult,
    AgentOperationName,
    CheckResult,
    MakeResult,
)
from ._evaluation_operations import (
    EvaluationDiffResult,
    compare_evaluation_baseline,
    discard_evaluation_output,
    evaluation_diff_result,
    evaluation_list_result,
    evaluation_run_result,
    load_evaluation_runner,
)
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
from ._verify_operations import VerificationResult, verification_result
from .compatibility import (
    render_compatibility_report,
    render_evaluation_compatibility_report,
    render_tool_compatibility_report,
)
from .errors import ConfigurationError
from .evaluations import EvaluationManifest, evaluation_manifest
from .openapi import openapi_schema
from .routes import RouteGroup
from .scaffold import app_files
from .snapshots import (
    describe_openapi_drift,
    evaluation_snapshot_diff,
    openapi_snapshot_diff,
    render_evaluation_snapshot,
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


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be an integer greater than zero")
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
_DEFAULT_EVALUATIONS = "app.server.evaluations:runner"
_DEFAULT_TASKS = "app.server.tasks:runner"
_DEFAULT_JOBS = "app.server.jobs:jobs"
_DEFAULT_TOOLS = "app.server.tools:tools"

_AGENT_COMMAND_OPERATIONS: dict[str, AgentOperationName] = {
    "make": "make",
    "routes": "routes",
    "tools": "tools",
    "map": "map",
    "openapi": "openapi",
    "doctor": "doctor",
    "check": "check",
    "verify": "verify",
    "preflight": "preflight",
}


def _structured_json_requested(argv: Sequence[str]) -> bool:
    """Return whether *argv* asks the CLI to reserve stdout for JSON."""
    if "--json" in argv or "--diff-format=json" in argv:
        return True
    return any(
        argument == "--diff-format"
        and index + 1 < len(argv)
        and argv[index + 1] == "json"
        for index, argument in enumerate(argv)
    )


def _agent_operation_from_argv(argv: Sequence[str]) -> AgentOperationName:
    """Identify the stable operation name without parsing untrusted arguments."""
    if not argv:
        return "cli"
    command = argv[0]
    if command == "eval":
        if len(argv) > 1 and argv[1] in {"list", "snapshot", "run"}:
            return cast(AgentOperationName, f"eval.{argv[1]}")
        return "cli"
    if command == "task":
        if len(argv) > 1 and argv[1] in {"list", "run"}:
            return cast(AgentOperationName, f"task.{argv[1]}")
        return "cli"
    return _AGENT_COMMAND_OPERATIONS.get(command, "cli")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    structured_json = _structured_json_requested(raw_argv)
    if structured_json and any(argument in {"-h", "--help"} for argument in raw_argv):
        _print_operation_error(
            operation=_agent_operation_from_argv(raw_argv),
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="Structured JSON output cannot be combined with help.",
            details=None,
        )
        return 2
    if structured_json:
        try:
            with (
                open(os.devnull, "w", encoding="utf-8") as parser_stderr,
                redirect_stderr(parser_stderr),
            ):
                args = parser.parse_args(raw_argv)
        except SystemExit as exc:
            if exc.code == 0:
                raise
            _print_operation_error(
                operation=_agent_operation_from_argv(raw_argv),
                code="TENCHI_CLI_INVALID_ARGUMENTS",
                message="Command arguments are invalid.",
                details=None,
            )
            return 2
    else:
        args = parser.parse_args(raw_argv)

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
            evaluations_target=args.evaluations,
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
            evaluations=args.evaluations,
            evaluation_snapshot=args.evaluation_snapshot,
            tools=args.tools,
            tool_snapshot=args.tool_snapshot,
            security_json=args.security,
            timeout_seconds=args.timeout,
            as_json=args.json,
        )
    if args.command == "verify":
        return _verify(
            base_ref=args.base_ref,
            routes=args.routes,
            evaluations=args.evaluations,
            tasks=args.tasks,
            jobs=args.jobs,
            tools=args.tools,
            title=args.title,
            version=args.version,
            description=args.description,
            snapshot=args.snapshot,
            evaluation_snapshot=args.evaluation_snapshot,
            tool_snapshot=args.tool_snapshot,
            allow_missing_evaluation_baseline=(args.allow_missing_evaluation_baseline),
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
    if args.command == "eval":
        if args.eval_command == "list":
            return _evaluation_list(args.target, as_json=args.json)
        if args.eval_command == "snapshot":
            return _evaluation_snapshot(
                args.target,
                write=args.write,
                check=args.check,
                diff=args.diff,
                diff_ref=args.diff_ref,
                snapshot=args.snapshot,
                diff_format=args.diff_format,
                allow_missing_baseline=args.allow_missing_baseline,
            )
        return _evaluation_run(
            args.target,
            args.name,
            concurrency=args.concurrency,
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
            evaluations=args.evaluations,
            tasks=args.tasks,
            jobs=args.jobs,
            tools=args.tools,
            allow_task_runs=args.allow_task_runs,
            allow_evaluation_runs=args.allow_evaluation_runs,
            tool_snapshot=args.tool_snapshot,
            evaluation_snapshot=args.evaluation_snapshot,
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
        "--evaluations",
        default=_DEFAULT_EVALUATIONS,
        help="module:attribute of the EvaluationRunner (default: %(default)s)",
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
        default=_DEFAULT_API_ROUTES,
        help="module:attribute of the RouteGroup (default: %(default)s)",
    )
    openapi_parser.add_argument(
        "--title",
        default=None,
        help="API title (default: discover OPENAPI_TITLE or use the directory name)",
    )
    openapi_parser.add_argument(
        "--version",
        default=None,
        help="API version (default: discover OPENAPI_VERSION or use 0.1.0)",
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
        help=(
            "Run formatting, lint, types, tests, doctor, and boundary snapshot checks"
        ),
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
        "--evaluations",
        default=_DEFAULT_EVALUATIONS,
        help="module:attribute of the EvaluationRunner (default: %(default)s)",
    )
    check_parser.add_argument(
        "--evaluation-snapshot",
        default="evaluations.json",
        help="Evaluation-policy snapshot to check (default: %(default)s)",
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

    verify_parser = subparsers.add_parser(
        "verify",
        help="Produce one receipt for checks, architecture, and contract compatibility",
    )
    verify_parser.add_argument(
        "--base-ref",
        required=True,
        metavar="REF",
        help="Git ref containing the historical OpenAPI and tool snapshots",
    )
    verify_parser.add_argument(
        "--routes",
        default=_DEFAULT_API_ROUTES,
        help="module:attribute of the API RouteGroup (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--evaluations",
        default=_DEFAULT_EVALUATIONS,
        help="module:attribute of the EvaluationRunner (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--tasks",
        default=_DEFAULT_TASKS,
        help="module:attribute of the TaskRunner (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--jobs",
        default=_DEFAULT_JOBS,
        help="module:attribute of the JobGroup (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--tools",
        default=_DEFAULT_TOOLS,
        help="module:attribute of the ToolGroup (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--title",
        default=None,
        help=(
            "OpenAPI title (default: literal OPENAPI_TITLE in the route module "
            "or the current directory name)"
        ),
    )
    verify_parser.add_argument(
        "--version",
        default=None,
        help=(
            "OpenAPI version (default: literal OPENAPI_VERSION in the route module "
            "or 0.1.0)"
        ),
    )
    verify_parser.add_argument(
        "--description",
        default=None,
        help=(
            "OpenAPI description (default: literal OPENAPI_DESCRIPTION in the "
            "route module)"
        ),
    )
    verify_parser.add_argument(
        "--security",
        default=None,
        metavar="JSON",
        help="OpenAPI security schemes as a JSON object",
    )
    verify_parser.add_argument(
        "--snapshot",
        default="openapi.json",
        help="Project-relative OpenAPI snapshot (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--tool-snapshot",
        default="tools.json",
        help="Project-relative application-tool snapshot (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--evaluation-snapshot",
        default="evaluations.json",
        help="Project-relative evaluation-policy snapshot (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--allow-missing-evaluation-baseline",
        action="store_true",
        help=(
            "Explicitly allow a missing historical evaluation snapshot during "
            "first adoption"
        ),
    )
    verify_parser.add_argument(
        "--timeout",
        default=600.0,
        type=_positive_float,
        metavar="SECONDS",
        help="Per-check-step timeout (default: %(default)s)",
    )
    verify_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the versioned verification receipt",
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

    evaluation_parser = subparsers.add_parser(
        "eval",
        help="Discover and run application-owned AI evaluations",
    )
    evaluation_subparsers = evaluation_parser.add_subparsers(
        dest="eval_command",
        required=True,
    )
    evaluation_list_parser = evaluation_subparsers.add_parser(
        "list",
        help="List registered evaluations without exposing case inputs",
    )
    evaluation_run_parser = evaluation_subparsers.add_parser(
        "run",
        help="Run one named evaluation or the complete registered group",
    )
    evaluation_snapshot_parser = evaluation_subparsers.add_parser(
        "snapshot",
        help="Print, write, check, or diff the evaluation-policy manifest",
    )
    evaluation_run_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Stable dotted evaluation name; omit to run every evaluation",
    )
    evaluation_run_parser.add_argument(
        "--concurrency",
        default=None,
        type=_positive_int,
        metavar="COUNT",
        help="Override the number of concurrently evaluated cases",
    )
    evaluation_run_parser.add_argument(
        "--timeout",
        default=None,
        type=_positive_float,
        metavar="SECONDS",
        help="Cap each case's declared timeout",
    )
    for operation_parser in (evaluation_list_parser, evaluation_run_parser):
        operation_parser.add_argument(
            "--evaluations",
            dest="target",
            default=_DEFAULT_EVALUATIONS,
            help=("module:attribute of the EvaluationRunner (default: %(default)s)"),
        )
        operation_parser.add_argument(
            "--json",
            action="store_true",
            help="Emit a versioned, payload-safe result",
        )
    evaluation_snapshot_parser.add_argument(
        "--evaluations",
        dest="target",
        default=_DEFAULT_EVALUATIONS,
        help="module:attribute of the EvaluationRunner (default: %(default)s)",
    )
    evaluation_snapshot_mode = evaluation_snapshot_parser.add_mutually_exclusive_group()
    evaluation_snapshot_mode.add_argument(
        "--write",
        default=None,
        metavar="PATH",
        help="Write the canonical evaluation-policy snapshot",
    )
    evaluation_snapshot_mode.add_argument(
        "--check",
        default=None,
        metavar="PATH",
        help="Fail if this snapshot differs from the generated manifest",
    )
    evaluation_snapshot_mode.add_argument(
        "--diff",
        default=None,
        metavar="BASELINE",
        help="Classify policy changes; fail on breaking or unknown changes",
    )
    evaluation_snapshot_mode.add_argument(
        "--diff-ref",
        default=None,
        metavar="REF",
        help="Classify policy changes from the snapshot committed at a Git ref",
    )
    evaluation_snapshot_parser.add_argument(
        "--snapshot",
        default=None,
        metavar="PATH",
        help="Snapshot path for --diff-ref (default: evaluations.json)",
    )
    evaluation_snapshot_parser.add_argument(
        "--diff-format",
        choices=("text", "json"),
        default="text",
        help="Compatibility report format (default: %(default)s)",
    )
    evaluation_snapshot_parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help=("Explicitly allow a missing --diff-ref snapshot during first adoption"),
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
        "--evaluations",
        default=_DEFAULT_EVALUATIONS,
        help="module:attribute used by evaluation tools (default: %(default)s)",
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
        "--allow-evaluation-runs",
        action="store_true",
        help=("Expose the cost-bearing evaluation_run tool (disabled by default)"),
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
        "--evaluation-snapshot",
        default="evaluations.json",
        help="Default project-relative evaluation baseline (default: %(default)s)",
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
    evaluations: str,
    tools: str,
    title: str | None,
    version: str | None,
    description: str | None,
    snapshot: str,
    evaluation_snapshot: str,
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
        evaluations=evaluations,
        evaluation_snapshot=evaluation_snapshot,
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


def _verify(
    *,
    base_ref: str,
    routes: str,
    evaluations: str,
    tasks: str,
    jobs: str,
    tools: str,
    title: str | None,
    version: str | None,
    description: str | None,
    snapshot: str,
    evaluation_snapshot: str,
    tool_snapshot: str,
    allow_missing_evaluation_baseline: bool,
    security_json: str | None,
    timeout_seconds: float,
    as_json: bool,
) -> int:
    with redirect_stdout(sys.stderr if as_json else sys.stdout):
        result = verification_result(
            Path.cwd(),
            base_ref=base_ref,
            routes=routes,
            evaluations=evaluations,
            tasks=tasks,
            jobs=jobs,
            tools=tools,
            title=title,
            version=version,
            description=description,
            snapshot=snapshot,
            evaluation_snapshot=evaluation_snapshot,
            tool_snapshot=tool_snapshot,
            allow_missing_evaluation_baseline=allow_missing_evaluation_baseline,
            security_json=security_json,
            timeout_seconds=timeout_seconds,
        )
    if as_json:
        _print_agent_json("verify", result.as_dict())
    else:
        _render_verification_result(result)
    return 0 if result.ok else 1


def _render_verification_result(result: VerificationResult) -> None:
    commit = result.baseline_commit or "unresolved"
    print(f"Baseline: {result.baseline_ref} -> {commit}")
    if result.check is not None:
        _render_check_result(result.check)
    if result.architecture is not None:
        status = "passed" if result.architecture.ok else "failed"
        print(
            f"[{status}] architecture "
            f"({len(result.architecture.diagnostics)} diagnostics, "
            f"{len(result.architecture.unresolved)} unresolved)"
        )
        for diagnostic in result.architecture.diagnostics:
            location = (
                f"{diagnostic.path}:{diagnostic.line}"
                if diagnostic.line is not None
                else diagnostic.path
            )
            print(f"  {location}  [{diagnostic.code}] {diagnostic.message}")
        for unresolved in result.architecture.unresolved:
            source = unresolved.source
            location = (
                f"{source.path}:{source.line}"
                if source.line is not None
                else source.path
            )
            print(f"  {location}  [{unresolved.code}] {unresolved.message}")
    if result.openapi is not None:
        status = "passed" if result.openapi.report.compatible else "failed"
        print(f"[{status}] OpenAPI compatibility ({result.openapi.report.status})")
        for change in result.openapi.report.changes:
            print(f"  [{change.severity}] {change.location}: {change.message}")
    if result.tools is not None:
        status = "passed" if result.tools.report.compatible else "failed"
        print(
            f"[{status}] application-tool compatibility ({result.tools.report.status})"
        )
        for change in result.tools.report.changes:
            print(f"  [{change.severity}] {change.location}: {change.message}")
    if result.evaluations is not None:
        status = "passed" if result.evaluations.report.compatible else "failed"
        print(
            f"[{status}] evaluation-policy compatibility "
            f"({result.evaluations.report.status})"
        )
        for change in result.evaluations.report.changes:
            print(f"  [{change.severity}] {change.location}: {change.message}")
    for error in result.errors:
        print(f"[failed] {error.stage}: {error.message}")
    summary = "passed" if result.ok else "failed"
    print()
    print(f"verify: {summary} in {result.duration_seconds:.2f}s")


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
        return _render_operation_error(
            operation="preflight",
            code=(
                "TENCHI_CLI_TARGET_LOAD_FAILED"
                if isinstance(exc, OperationError)
                else "TENCHI_CLI_CONFIGURATION_INVALID"
            ),
            message=(
                "Could not load the configured preflight target."
                if isinstance(exc, OperationError)
                else "The preflight configuration is invalid."
            ),
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi preflight: {exc}",
        )

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


def _evaluation_list(target: str, *, as_json: bool) -> int:
    try:
        with discard_evaluation_output():
            runner = load_evaluation_runner(Path.cwd(), target)
            result = evaluation_list_result(Path.cwd(), target, runner)
    except (OperationError, ConfigurationError) as exc:
        return _render_operation_error(
            operation="eval.list",
            code=(
                "TENCHI_CLI_TARGET_LOAD_FAILED"
                if isinstance(exc, OperationError)
                else "TENCHI_CLI_CONFIGURATION_INVALID"
            ),
            message=(
                "Could not load the configured evaluation target."
                if isinstance(exc, OperationError)
                else "The evaluation configuration is invalid."
            ),
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi eval list: {exc}",
        )

    if as_json:
        _print_agent_json("evaluation_list", result.as_dict())
        return 0
    if not result.evaluations:
        print("No evaluations are registered.")
        return 0
    width = max(len(item.name) for item in result.evaluations)
    for item in result.evaluations:
        metric_names = ", ".join(metric.name for metric in item.metrics)
        description = f"  {item.description}" if item.description else ""
        print(
            f"{item.name:<{width}}  {item.kind}  "
            f"{len(item.cases)} cases  metrics: {metric_names}{description}"
        )
    return 0


def _evaluation_snapshot(
    target: str,
    *,
    write: str | None,
    check: str | None,
    diff: str | None,
    diff_ref: str | None,
    snapshot: str | None,
    diff_format: str,
    allow_missing_baseline: bool,
) -> int:
    as_json = diff_format == "json"
    if diff is None and diff_ref is None and diff_format != "text":
        return _render_operation_error(
            operation="eval.snapshot",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--diff-format requires --diff or --diff-ref.",
            details=None,
            as_json=as_json,
            human_message=(
                "tenchi eval snapshot: --diff-format requires --diff or --diff-ref"
            ),
        )
    if snapshot is not None and diff_ref is None:
        return _render_operation_error(
            operation="eval.snapshot",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--snapshot requires --diff-ref.",
            details=None,
            as_json=as_json,
            human_message="tenchi eval snapshot: --snapshot requires --diff-ref",
        )
    if allow_missing_baseline and diff_ref is None:
        return _render_operation_error(
            operation="eval.snapshot",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--allow-missing-baseline requires --diff-ref.",
            details=None,
            as_json=as_json,
            human_message=(
                "tenchi eval snapshot: --allow-missing-baseline requires --diff-ref"
            ),
        )

    try:
        with discard_evaluation_output():
            runner = load_evaluation_runner(Path.cwd(), target)
            manifest = evaluation_manifest(runner.evaluations)
    except (OperationError, ConfigurationError) as exc:
        return _render_operation_error(
            operation="eval.snapshot",
            code=(
                "TENCHI_CLI_TARGET_LOAD_FAILED"
                if isinstance(exc, OperationError)
                else "TENCHI_CLI_CONFIGURATION_INVALID"
            ),
            message=(
                "Could not load the configured evaluation target."
                if isinstance(exc, OperationError)
                else "The evaluation configuration is invalid."
            ),
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi eval snapshot: {exc}",
        )

    rendered = render_evaluation_snapshot(manifest)
    if write is not None:
        try:
            Path(write).write_text(rendered, encoding="utf-8")
        except OSError as exc:
            _fail(f"tenchi eval snapshot: could not write snapshot {write!r}: {exc}")
            return 1
        print(f"Wrote {write}")
        return 0
    if check is not None:
        return _check_evaluation_snapshot(Path(check), rendered, manifest)
    if diff is not None:
        return _diff_evaluation_snapshot(
            Path(diff),
            manifest,
            output_format=diff_format,
        )
    if diff_ref is not None:
        try:
            result = evaluation_diff_result(
                Path.cwd(),
                evaluations=target,
                snapshot=Path(snapshot or "evaluations.json"),
                ref=diff_ref,
                allow_missing_baseline=allow_missing_baseline,
            )
        except OperationError as exc:
            return _render_operation_error(
                operation="eval.snapshot",
                code="TENCHI_CLI_OPERATION_FAILED",
                message="Could not compare the evaluation-policy baseline.",
                details=None,
                as_json=as_json,
                human_message=f"tenchi eval snapshot: {exc}",
            )
        return _render_evaluation_diff(result, output_format=diff_format)
    sys.stdout.write(rendered)
    return 0


def _check_evaluation_snapshot(
    path: Path,
    rendered: str,
    current: EvaluationManifest,
) -> int:
    try:
        expected = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _fail(f"tenchi eval snapshot: could not read snapshot {str(path)!r}: {exc}")
        _fail(
            f"Rerun the same command with --write {path} instead of --check "
            "to create it."
        )
        return 1
    if expected == rendered:
        print(f"Evaluation snapshot matches {path}")
        return 0

    _fail(f"tenchi eval snapshot: snapshot differs: {path}")
    try:
        result = compare_evaluation_baseline(
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
    diff = evaluation_snapshot_diff(expected, rendered, snapshot_path=str(path))
    if diff:
        print(file=sys.stderr)
        print(diff, file=sys.stderr, end="" if diff.endswith("\n") else "\n")
    print(file=sys.stderr)
    _fail(
        f"Run the same command with --write {path} instead of --check "
        "to accept this change."
    )
    return 1


def _diff_evaluation_snapshot(
    path: Path,
    current: EvaluationManifest,
    *,
    output_format: str,
) -> int:
    try:
        baseline_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return _render_operation_error(
            operation="eval.snapshot",
            code="TENCHI_CLI_SNAPSHOT_READ_FAILED",
            message="Could not read the evaluation-policy baseline.",
            details={"path": str(path)},
            as_json=output_format == "json",
            human_message=(
                f"tenchi eval snapshot: could not read baseline {str(path)!r}: {exc}"
            ),
        )
    try:
        result = compare_evaluation_baseline(
            Path.cwd(),
            baseline_text=baseline_text,
            baseline_label=str(path),
            current=current,
        )
    except OperationError as exc:
        return _render_operation_error(
            operation="eval.snapshot",
            code="TENCHI_CLI_OPERATION_FAILED",
            message="Could not compare the evaluation-policy baseline.",
            details=None,
            as_json=output_format == "json",
            human_message=f"tenchi eval snapshot: {exc}",
        )
    return _render_evaluation_diff(result, output_format=output_format)


def _render_evaluation_diff(
    result: EvaluationDiffResult,
    *,
    output_format: str,
) -> int:
    if output_format == "json":
        _print_agent_json("evaluation_diff", result.as_dict())
    else:
        sys.stdout.write(
            render_evaluation_compatibility_report(
                result.report,
                baseline_path=result.baseline,
            )
        )
    return 0 if result.report.compatible else 1


def _evaluation_run(
    target: str,
    name: str | None,
    *,
    concurrency: int | None,
    timeout: float | None,
    as_json: bool,
) -> int:
    try:
        with discard_evaluation_output():
            runner = load_evaluation_runner(Path.cwd(), target)
            result = asyncio.run(
                evaluation_run_result(
                    Path.cwd(),
                    target,
                    runner,
                    name=name,
                    concurrency=concurrency,
                    timeout=timeout,
                )
            )
    except (OperationError, ConfigurationError) as exc:
        return _render_operation_error(
            operation="eval.run",
            code=(
                "TENCHI_CLI_TARGET_LOAD_FAILED"
                if isinstance(exc, OperationError)
                else "TENCHI_CLI_CONFIGURATION_INVALID"
            ),
            message=(
                "Could not load the configured evaluation target."
                if isinstance(exc, OperationError)
                else "The evaluation configuration is invalid."
            ),
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi eval run: {exc}",
        )

    if as_json:
        _print_agent_json("evaluation_run", result.as_dict())
    elif result.error is not None:
        _fail(f"tenchi eval run: [{result.error.code}] {result.error.message}")
    else:
        for item in result.evaluations:
            status = "passed" if item.ok else "failed"
            print(
                f"[{status}] {item.name} ({len(item.cases)} cases in "
                f"{item.duration_seconds:.2f}s)"
            )
            for metric in item.metrics:
                average = (
                    "unavailable" if metric.average is None else f"{metric.average:.3f}"
                )
                metric_status = "passed" if metric.passed else "failed"
                print(
                    f"  [{metric_status}] {metric.name}: {average} "
                    f"(threshold {metric.threshold:.3f}, "
                    f"{metric.samples} samples)"
                )
            for case in item.cases:
                if case.status != "completed":
                    print(
                        f"  [{case.status}] {case.name} "
                        f"[{case.failure_code or 'EVALUATION_FAILED'}]"
                    )
            if not item.budget.passed:
                reason = (
                    "exceeded"
                    if item.budget.status == "exceeded"
                    else "could not be verified"
                )
                print(f"  [failed] declared token or cost budget {reason}")
        summary = "passed" if result.ok else "failed"
        print()
        print(
            f"eval: {summary} ({len(result.evaluations)} evaluations in "
            f"{result.duration_seconds:.2f}s)"
        )
    return 0 if result.ok else 1


def _routes(target: str, *, as_json: bool = False) -> int:
    try:
        with redirect_stdout(sys.stderr if as_json else sys.stdout):
            group = load_route_group(Path.cwd(), target)
    except OperationError as exc:
        return _render_operation_error(
            operation="routes",
            code="TENCHI_CLI_TARGET_LOAD_FAILED",
            message="Could not load the configured route target.",
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi routes: {exc}",
        )

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
    evaluations_target: str,
    tasks_target: str,
    jobs_target: str,
    tools_target: str,
    feature: str | None,
    kinds: Sequence[AppMapNodeKind] | None,
    as_json: bool,
) -> int:
    output = sys.stderr if as_json else sys.stdout
    try:
        with redirect_stdout(output):
            group = load_route_group(Path.cwd(), target)
        with discard_evaluation_output():
            evaluation_runner = load_evaluation_runner(Path.cwd(), evaluations_target)
        with redirect_stdout(output):
            runner = load_task_runner(Path.cwd(), tasks_target)
            jobs = load_job_group(Path.cwd(), jobs_target)
            tools = load_tool_group(Path.cwd(), tools_target)
    except OperationError as exc:
        return _render_operation_error(
            operation="map",
            code="TENCHI_CLI_TARGET_LOAD_FAILED",
            message="Could not load an application-map composition target.",
            details=None,
            as_json=as_json,
            human_message=f"tenchi map: {exc}",
        )

    with redirect_stdout(output):
        result = map_app(
            Path.cwd(),
            group,
            runner.tasks,
            jobs,
            tools,
            evaluation_runner.evaluations,
        )
    if feature is not None:
        features = sorted(node.name for node in result.nodes if node.kind == "feature")
        if feature not in features:
            available = ", ".join(features) if features else "none"
            return _render_operation_error(
                operation="map",
                code="TENCHI_CLI_SELECTION_NOT_FOUND",
                message="The requested application-map feature does not exist.",
                details={"feature": feature, "available_features": features},
                as_json=as_json,
                human_message=(
                    f"tenchi map: unknown feature {feature!r}; "
                    f"available features: {available}"
                ),
            )
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
        return _render_operation_error(
            operation="task.list",
            code="TENCHI_CLI_TARGET_LOAD_FAILED",
            message="Could not load the configured task target.",
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi task list: {exc}",
        )
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
        return _render_operation_error(
            operation="task.run",
            code="TENCHI_CLI_TARGET_LOAD_FAILED",
            message="Could not load the configured task target.",
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi task run: {exc}",
        )
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
    structured = as_json or diff_format == "json"
    has_snapshot_mode = any(
        value is not None for value in (write, check, diff, diff_ref)
    )
    if as_json and has_snapshot_mode:
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message=(
                "--json cannot be combined with snapshot write, check, or diff modes."
            ),
            details=None,
            as_json=True,
            human_message=(
                "tenchi tools: --json cannot be combined with snapshot write, "
                "check, or diff modes"
            ),
        )
    if diff is None and diff_ref is None and diff_format != "text":
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--diff-format requires --diff or --diff-ref.",
            details=None,
            as_json=structured,
            human_message="tenchi tools: --diff-format requires --diff or --diff-ref",
        )
    if snapshot is not None and diff_ref is None:
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--snapshot requires --diff-ref.",
            details=None,
            as_json=structured,
            human_message="tenchi tools: --snapshot requires --diff-ref",
        )

    try:
        with redirect_stdout(sys.stderr):
            group = load_tool_group(Path.cwd(), target)
            manifest = tool_manifest(group)
    except OperationError as exc:
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_TARGET_LOAD_FAILED",
            message="Could not load the configured application-tool target.",
            details={"target": target},
            as_json=structured,
            human_message=f"tenchi tools: {exc}",
        )

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
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_SNAPSHOT_READ_FAILED",
            message="Could not read the application-tool baseline.",
            details={"path": str(path)},
            as_json=output_format == "json",
            human_message=(
                f"tenchi tools: could not read baseline {str(path)!r}: {exc}"
            ),
        )
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
        baseline = read_git_snapshot(
            Path.cwd(),
            ref=ref,
            snapshot=snapshot,
        )
        baseline_text = baseline.text
        baseline_label = baseline.label
    except OperationError as exc:
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_OPERATION_FAILED",
            message="Could not read the application-tool baseline from Git.",
            details=None,
            as_json=output_format == "json",
            human_message=f"tenchi tools: {exc}",
        )
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
        return _render_operation_error(
            operation="tools",
            code="TENCHI_CLI_OPERATION_FAILED",
            message="Could not compare the application-tool baseline.",
            details=None,
            as_json=output_format == "json",
            human_message=f"tenchi tools: {exc}",
        )

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
    version: str | None,
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
    as_json = diff_format == "json"
    if diff is None and diff_ref is None and diff_format != "text":
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--diff-format requires --diff or --diff-ref.",
            details=None,
            as_json=as_json,
            human_message=(
                "tenchi openapi: --diff-format requires --diff or --diff-ref"
            ),
        )
    if snapshot is not None and diff_ref is None:
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_INVALID_ARGUMENTS",
            message="--snapshot requires --diff-ref.",
            details=None,
            as_json=as_json,
            human_message="tenchi openapi: --snapshot requires --diff-ref",
        )

    root = Path.cwd()
    title, version, description, security_json = openapi_defaults(
        root,
        routes=target,
        title=title,
        version=version,
        description=description,
        security_json=security_json,
    )
    try:
        with redirect_stdout(sys.stderr):
            group = load_route_group(Path.cwd(), target)
    except OperationError as exc:
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_TARGET_LOAD_FAILED",
            message="Could not load the configured OpenAPI route target.",
            details={"target": target},
            as_json=as_json,
            human_message=f"tenchi openapi: {exc}",
        )

    security: Mapping[str, Mapping[str, Any]] | None = None
    if security_json is not None:
        try:
            parsed_security: object = json.loads(security_json)
        except json.JSONDecodeError as exc:
            return _render_operation_error(
                operation="openapi",
                code="TENCHI_CLI_INVALID_ARGUMENTS",
                message="--security must be valid JSON.",
                details={"line": exc.lineno, "column": exc.colno},
                as_json=as_json,
                human_message=(
                    "tenchi openapi: --security must be valid JSON "
                    f"(line {exc.lineno}, column {exc.colno})"
                ),
            )
        if not isinstance(parsed_security, Mapping):
            return _render_operation_error(
                operation="openapi",
                code="TENCHI_CLI_INVALID_ARGUMENTS",
                message="--security must be a JSON object.",
                details=None,
                as_json=as_json,
                human_message="tenchi openapi: --security must be a JSON object",
            )
        security = cast(Mapping[str, Mapping[str, Any]], parsed_security)

    try:
        with redirect_stdout(sys.stderr):
            document = openapi_schema(
                group,
                title=title,
                version=version,
                description=description,
                security=security,
            )
    except ConfigurationError as exc:
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_CONFIGURATION_INVALID",
            message="The OpenAPI configuration is invalid.",
            details=None,
            as_json=as_json,
            human_message=f"tenchi openapi: {exc}",
        )
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
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_SNAPSHOT_READ_FAILED",
            message="Could not read the OpenAPI baseline.",
            details={"path": str(path)},
            as_json=output_format == "json",
            human_message=(
                f"tenchi openapi: could not read baseline {str(path)!r}: {exc}"
            ),
        )
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
        baseline = read_git_snapshot(Path.cwd(), ref=ref, snapshot=snapshot)
        baseline_text = baseline.text
        baseline_label = baseline.label
    except OperationError as exc:
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_OPERATION_FAILED",
            message="Could not read the OpenAPI baseline from Git.",
            details=None,
            as_json=output_format == "json",
            human_message=f"tenchi openapi: {exc}",
        )
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
        return _render_operation_error(
            operation="openapi",
            code="TENCHI_CLI_OPERATION_FAILED",
            message="Could not compare the OpenAPI baseline.",
            details=None,
            as_json=output_format == "json",
            human_message=f"tenchi openapi: {exc}",
        )

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
    evaluations: str,
    tasks: str,
    jobs: str,
    tools: str,
    allow_task_runs: bool,
    allow_evaluation_runs: bool,
    snapshot: str,
    tool_snapshot: str,
    evaluation_snapshot: str,
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
                evaluations=evaluations,
                tasks=tasks,
                jobs=jobs,
                tools=tools,
                allow_task_runs=allow_task_runs,
                allow_evaluation_runs=allow_evaluation_runs,
                snapshot=snapshot,
                tool_snapshot=tool_snapshot,
                evaluation_snapshot=evaluation_snapshot,
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


def _print_operation_error(
    *,
    operation: AgentOperationName,
    code: AgentOperationErrorCode,
    message: str,
    details: AgentOperationErrorDetails | None,
) -> None:
    _print_agent_json(
        "operation_error",
        AgentOperationErrorResult(
            operation=operation,
            code=code,
            message=message,
            details=details,
        ).as_dict(),
    )


def _render_operation_error(
    *,
    operation: AgentOperationName,
    code: AgentOperationErrorCode,
    message: str,
    details: AgentOperationErrorDetails | None,
    as_json: bool,
    human_message: str,
) -> int:
    if as_json:
        _print_operation_error(
            operation=operation,
            code=code,
            message=message,
            details=details,
        )
    else:
        _fail(human_message)
    return 1


def _fail(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
