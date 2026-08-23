"""Command-line interface for the repository-owned agent benchmark."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, cast

from .core import (
    BenchmarkError,
    evaluate_workspace,
    list_tasks,
    load_task,
    prepare_workspace,
    read_results,
    run_benchmark,
    summarize_results,
    write_payload,
)
from .models import (
    AgentResult,
    AgentStatus,
    BenchmarkTaskList,
    parse_state,
    render_payload,
    validated_interface,
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    benchmark_arguments, agent_command = _split_agent_command(argv)
    arguments = parser.parse_args(benchmark_arguments)
    if arguments.command == "run":
        arguments.agent_command = agent_command
    try:
        return _dispatch(arguments)
    except (BenchmarkError, OSError, UnicodeError, ValueError) as exc:
        print(f"benchmark: {exc}", file=sys.stderr)
        return 1


def _dispatch(arguments: argparse.Namespace) -> int:
    command = cast(str, arguments.command)
    if command == "list":
        tasks = list_tasks()
        if arguments.json:
            print(render_payload(BenchmarkTaskList(tasks)), end="")
        else:
            for task in tasks:
                print(f"{task.id}: {task.title}")
        return 0
    if command == "prepare":
        task = load_task(arguments.task)
        requested_workspace = Path(arguments.workspace)
        state_path = Path(arguments.state)
        _require_outside_workspace(
            state_path,
            requested_workspace,
            label="state",
        )
        state = prepare_workspace(
            task,
            requested_workspace,
            tenchi_root=Path(arguments.tenchi_root),
            sync=not arguments.no_sync,
        )
        write_payload(state_path, state)
        print(render_payload(state), end="")
        return 0
    if command == "evaluate":
        state_path = Path(arguments.state)
        state = parse_state(state_path.read_text(encoding="utf-8"))
        workspace = Path(state.workspace)
        _require_outside_workspace(state_path, workspace, label="state")
        output = Path(arguments.output)
        _require_outside_workspace(output, workspace, label="result")
        task = load_task(state.task)
        agent = AgentResult(
            label=arguments.agent_label,
            interface=validated_interface(arguments.interface),
            attempt=arguments.attempt,
            interventions=arguments.interventions,
            status=cast(AgentStatus, arguments.agent_status),
            exit_code=arguments.agent_exit_code,
            duration_seconds=arguments.agent_duration,
        )
        result = evaluate_workspace(task, state, agent=agent)
        write_payload(output, result)
        print(render_payload(result), end="")
        return 0 if result.ok else 1
    if command == "run":
        task = load_task(arguments.task)
        agent_command = tuple(arguments.agent_command)
        if agent_command and agent_command[0] == "--":
            agent_command = agent_command[1:]
        if not agent_command:
            raise BenchmarkError("run requires an agent command after '--'")
        workspace = Path(arguments.workspace)
        output = Path(arguments.output)
        _require_outside_workspace(output, workspace, label="result")
        result = run_benchmark(
            task,
            workspace,
            tenchi_root=Path(arguments.tenchi_root),
            command=agent_command,
            label=arguments.agent_label,
            interface=validated_interface(arguments.interface),
            attempt=arguments.attempt,
            interventions=arguments.interventions,
            sync=not arguments.no_sync,
            timeout_seconds=arguments.timeout,
        )
        write_payload(output, result)
        print(render_payload(result), end="")
        return 0 if result.ok else 1
    if command == "report":
        results = read_results(tuple(Path(value) for value in arguments.results))
        summary = summarize_results(results)
        if arguments.json:
            print(render_payload(summary), end="")
        else:
            print(
                f"benchmark: {summary.passed}/{summary.runs} passed; "
                f"{summary.first_passed}/{summary.runs} first-pass"
            )
            for task in summary.tasks:
                print(
                    f"  {task.task}@{task.task_digest[7:19]}: "
                    f"{task.passed}/{task.runs} passed; "
                    f"{task.first_passed}/{task.runs} first-pass"
                )
        return 0 if summary.passed == summary.runs else 1
    return _unreachable(command)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.agent_changes",
        description="Run isolated coding-agent changes against hidden Tenchi tests.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List benchmark tasks")
    list_parser.add_argument("--json", action="store_true")

    prepare = subparsers.add_parser(
        "prepare", help="Create a workspace for an externally managed agent run"
    )
    prepare.add_argument("task")
    prepare.add_argument("workspace")
    prepare.add_argument("--state", required=True)
    _add_workspace_options(prepare)

    evaluate = subparsers.add_parser(
        "evaluate", help="Evaluate an externally managed agent workspace"
    )
    evaluate.add_argument("--state", required=True)
    evaluate.add_argument("--output", required=True)
    _add_agent_metadata(evaluate, default_status="external")

    run = subparsers.add_parser(
        "run",
        help="Prepare, invoke an stdin-driven agent, and evaluate it",
        epilog="Place the agent command after '--'.",
    )
    run.add_argument("task")
    run.add_argument("workspace")
    run.add_argument("--output", required=True)
    run.add_argument("--timeout", type=_positive_float, default=None)
    _add_workspace_options(run)
    _add_agent_metadata(run, default_status=None)

    report = subparsers.add_parser("report", help="Aggregate result JSON files")
    report.add_argument("results", nargs="+")
    report.add_argument("--json", action="store_true")
    return parser


def _split_agent_command(argv: list[str] | None) -> tuple[list[str] | None, list[str]]:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0] != "run" or "--" not in values:
        return argv, []
    separator = values.index("--")
    return values[:separator], values[separator + 1 :]


def _add_workspace_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tenchi-root",
        default=str(Path(__file__).parents[2]),
        help="Checkout containing this benchmark harness (normally auto-detected)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip uv sync; useful only for harness development",
    )


def _add_agent_metadata(
    parser: argparse.ArgumentParser,
    *,
    default_status: AgentStatus | None,
) -> None:
    parser.add_argument("--agent-label", default="unspecified")
    parser.add_argument(
        "--interface", choices=("cli", "mcp", "mixed", "unknown"), default="unknown"
    )
    parser.add_argument("--attempt", type=_positive_int, default=1)
    parser.add_argument("--interventions", type=_non_negative_int, default=0)
    if default_status is not None:
        parser.add_argument(
            "--agent-status",
            choices=("passed", "failed", "timed_out", "external"),
            default=default_status,
        )
        parser.add_argument("--agent-exit-code", type=int, default=None)
        parser.add_argument("--agent-duration", type=_non_negative_float, default=0.0)


def _require_outside_workspace(path: Path, workspace: Path, *, label: str) -> None:
    destination = path.resolve()
    root = workspace.resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        return
    raise BenchmarkError(
        f"benchmark {label} must be stored outside the agent workspace"
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return parsed


def _unreachable(command: str) -> NoReturn:
    raise AssertionError(f"unreachable benchmark command {command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
