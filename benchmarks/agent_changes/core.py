"""Workspace isolation, evaluation, and reporting for agent-change benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal, cast

from tenchi._agent_protocol import validate_agent_result
from tenchi._git import git_command, git_environment
from tenchi.scaffold import app_files

from .models import (
    AgentInterface,
    AgentResult,
    AgentStatus,
    BenchmarkResult,
    BenchmarkState,
    BenchmarkSummary,
    BenchmarkTask,
    SourceResult,
    StepName,
    StepResult,
    TaskSummary,
    parse_result,
    render_payload,
    validate_benchmark_result,
)
from .models import (
    validate_agent_result as validate_benchmark_agent_result,
)

_BENCHMARK_ROOT = Path(__file__).parent
_REPOSITORY_ROOT = _BENCHMARK_ROOT.parents[1]
_TASKS_ROOT = _BENCHMARK_ROOT / "tasks"
_TASK_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")
_COMMIT_LENGTHS = {40, 64}
_MAX_CAPTURE_BYTES = 2_000_000
_MAX_REPORTED_PATHS = 200
_MAX_AGENT_TEST_FILES = 200
_REQUIRED_COMMAND_TIMEOUT_SECONDS = 600.0
_EVALUATOR_PYTHON = os.path.abspath(sys.executable)
_PYTEST_CONFIG = _BENCHMARK_ROOT / "pytest.ini"
_PROTECTED_PATHS = ("TASK.md", "pyproject.toml", "tenchi.toml", "uv.lock")
_HIDDEN_TEST_PREFIXES = (
    "tests/tenchi_benchmark_hidden/",
    "tests/tenchi_benchmark_hidden_",
)


class BenchmarkError(RuntimeError):
    """The benchmark could not prepare or evaluate a trustworthy run."""


def list_tasks(*, tasks_root: Path = _TASKS_ROOT) -> tuple[BenchmarkTask, ...]:
    """Load every task in stable identifier order."""
    if not tasks_root.is_dir():
        raise BenchmarkError(f"benchmark task root not found: {tasks_root}")
    tasks = tuple(
        load_task(path.name, tasks_root=tasks_root)
        for path in sorted(tasks_root.iterdir(), key=lambda item: item.name)
        if path.is_dir()
    )
    if not tasks:
        raise BenchmarkError("benchmark task root contains no tasks")
    return tasks


def load_task(task_id: str, *, tasks_root: Path = _TASKS_ROOT) -> BenchmarkTask:
    """Load one exact task definition without allowing path traversal."""
    if not _valid_task_id(task_id):
        raise BenchmarkError(f"invalid benchmark task id {task_id!r}")
    root = (tasks_root / task_id).resolve()
    try:
        root.relative_to(tasks_root.resolve())
    except ValueError as exc:
        raise BenchmarkError("benchmark task must stay inside the task root") from exc
    definition_path = root / "task.toml"
    try:
        definition = definition_path.read_text(encoding="utf-8")
        raw = cast(dict[str, object], tomllib.loads(definition))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise BenchmarkError(f"could not read benchmark task {task_id!r}") from exc
    expected = {
        "schema_version",
        "id",
        "title",
        "description",
        "prompt",
        "hidden_tests",
        "agent_timeout_seconds",
        "evaluation_timeout_seconds",
    }
    if set(raw) != expected:
        raise BenchmarkError(
            f"benchmark task {task_id!r} has missing or unsupported fields"
        )
    if (
        not isinstance(raw["schema_version"], int)
        or isinstance(raw["schema_version"], bool)
        or raw["schema_version"] != 1
        or raw["id"] != task_id
    ):
        raise BenchmarkError(
            f"benchmark task {task_id!r} has an unsupported version or identity"
        )
    title = _required_string(raw["title"], label="title")
    description = _required_string(raw["description"], label="description")
    prompt_relative = _required_string(raw["prompt"], label="prompt")
    prompt_path = _task_path(root, prompt_relative, label="prompt")
    hidden_raw = raw["hidden_tests"]
    if not isinstance(hidden_raw, list) or not hidden_raw:
        raise BenchmarkError("benchmark task hidden_tests must be a non-empty list")
    hidden_values = cast(list[object], hidden_raw)
    hidden_tests = tuple(
        _task_path(
            root,
            _required_string(value, label="hidden test"),
            label="hidden test",
        )
        for value in hidden_values
    )
    if len(set(hidden_tests)) != len(hidden_tests):
        raise BenchmarkError("benchmark task hidden_tests must be unique")
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BenchmarkError(f"could not read task prompt {prompt_relative!r}") from exc
    if not prompt.strip():
        raise BenchmarkError("benchmark task prompt must not be empty")
    for hidden in hidden_tests:
        if not hidden.is_file() or not hidden.name.endswith(".py.tmpl"):
            raise BenchmarkError(
                "benchmark hidden tests must be existing .py.tmpl files"
            )
    return BenchmarkTask(
        id=task_id,
        digest=_task_digest(
            root,
            (definition_path, prompt_path, *hidden_tests),
        ),
        title=title,
        description=description,
        prompt=prompt,
        prompt_path=prompt_path,
        hidden_tests=hidden_tests,
        agent_timeout_seconds=_positive_seconds(
            raw["agent_timeout_seconds"], label="agent_timeout_seconds"
        ),
        evaluation_timeout_seconds=_positive_seconds(
            raw["evaluation_timeout_seconds"], label="evaluation_timeout_seconds"
        ),
    )


def prepare_workspace(
    task: BenchmarkTask,
    workspace: Path,
    *,
    tenchi_root: Path,
    sync: bool = True,
) -> BenchmarkState:
    """Create a fresh generated application and immutable Git baseline."""
    destination = workspace.resolve()
    source_root = tenchi_root.resolve()
    if source_root != _REPOSITORY_ROOT.resolve():
        raise BenchmarkError(
            "benchmark Tenchi root must be the checkout containing this harness"
        )
    if destination.exists():
        raise BenchmarkError(f"benchmark workspace already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.prepare-", dir=destination.parent)
    )
    moved = False
    try:
        files = app_files("tenchi_benchmark")
        files["pyproject.toml"] = _local_project(files["pyproject.toml"], source_root)
        files["TASK.md"] = task.prompt
        for relative, content in files.items():
            path = (staging / relative).resolve()
            try:
                path.relative_to(staging)
            except ValueError as exc:
                raise BenchmarkError("generated file escaped the workspace") from exc
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        staging.replace(destination)
        moved = True
        if sync:
            _run_required(
                ("uv", "sync"),
                cwd=destination,
                environment=_workspace_environment(destination),
            )
        _run_git_required(destination, "init", "--quiet", "--initial-branch=main")
        _run_git_required(destination, "add", "--all")
        _run_git_required(
            destination,
            "-c",
            "user.name=Tenchi Benchmark",
            "-c",
            "user.email=benchmark@invalid.example",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "--no-verify",
            "-m",
            "benchmark baseline",
        )
        baseline = _git_text(destination, "rev-parse", "HEAD")
        _validate_commit(baseline)
    except BaseException:
        shutil.rmtree(destination if moved else staging, ignore_errors=True)
        raise
    return BenchmarkState(
        task=task.id,
        task_digest=task.digest,
        workspace=str(destination),
        baseline_commit=baseline,
    )


def run_benchmark(
    task: BenchmarkTask,
    workspace: Path,
    *,
    tenchi_root: Path,
    command: Sequence[str],
    label: str,
    interface: AgentInterface,
    attempt: int,
    interventions: int,
    sync: bool = True,
    timeout_seconds: float | None = None,
) -> BenchmarkResult:
    """Prepare, run one stdin-driven agent command, then evaluate its work."""
    if not command:
        raise BenchmarkError("agent command must not be empty")
    timeout = _positive_seconds(
        task.agent_timeout_seconds if timeout_seconds is None else timeout_seconds,
        label="agent timeout",
    )
    metadata = validate_benchmark_agent_result(
        AgentResult(
            label=_required_string(label, label="agent label"),
            interface=interface,
            attempt=_positive_int(attempt, label="attempt"),
            interventions=_non_negative_int(interventions, label="interventions"),
            status="external",
            exit_code=None,
            duration_seconds=0.0,
        )
    )
    state = prepare_workspace(task, workspace, tenchi_root=tenchi_root, sync=sync)
    prompt_path = Path(state.workspace) / "TASK.md"
    environment = _workspace_environment(
        Path(state.workspace),
        {
            "TENCHI_BENCHMARK_TASK": task.id,
            "TENCHI_BENCHMARK_PROMPT_PATH": str(prompt_path),
            "TENCHI_BENCHMARK_WORKSPACE": state.workspace,
        },
    )
    process = _run_process(
        tuple(command),
        cwd=Path(state.workspace),
        timeout_seconds=timeout,
        stdin=task.prompt.encode("utf-8"),
        capture_stdout=False,
        environment=environment,
    )
    status: AgentStatus = (
        "timed_out"
        if process.timed_out
        else "passed"
        if process.exit_code == 0
        else "failed"
    )
    agent = AgentResult(
        label=metadata.label,
        interface=metadata.interface,
        attempt=metadata.attempt,
        interventions=metadata.interventions,
        status=status,
        exit_code=process.exit_code,
        duration_seconds=process.duration_seconds,
    )
    return evaluate_workspace(task, state, agent=agent)


def evaluate_workspace(
    task: BenchmarkTask,
    state: BenchmarkState,
    *,
    agent: AgentResult | None = None,
) -> BenchmarkResult:
    """Inject hidden tests and return a payload-safe evaluation result."""
    if state.task != task.id:
        raise BenchmarkError(
            f"benchmark state belongs to {state.task!r}, not {task.id!r}"
        )
    if state.task_digest != task.digest:
        raise BenchmarkError(
            f"benchmark task {task.id!r} changed after the workspace was prepared"
        )
    workspace = Path(state.workspace).resolve()
    if not workspace.is_dir():
        raise BenchmarkError(f"benchmark workspace not found: {workspace}")
    _validate_commit(state.baseline_commit)
    _validate_workspace_baseline(workspace, state.baseline_commit)
    source, changed_paths = _source_result(workspace, state.baseline_commit)
    effective_agent = validate_benchmark_agent_result(
        agent
        or AgentResult(
            label="external",
            interface="unknown",
            attempt=1,
            interventions=0,
            status="external",
            exit_code=None,
            duration_seconds=0.0,
        )
    )
    integrity = _task_integrity(task, workspace, state.baseline_commit)
    agent_tests = _evaluate_agent_tests(
        changed_paths,
        cwd=workspace,
        timeout_seconds=task.evaluation_timeout_seconds,
    )
    hidden_root = _install_hidden_tests(task, workspace)
    hidden = _evaluate_command(
        "hidden_acceptance",
        _pytest_command(
            str(hidden_root.relative_to(workspace)),
            confcutdir=hidden_root,
        ),
        cwd=workspace,
        timeout_seconds=task.evaluation_timeout_seconds,
    )
    verify = _evaluate_verify(
        workspace,
        baseline_commit=state.baseline_commit,
        timeout_seconds=task.evaluation_timeout_seconds,
    )
    steps = (integrity, agent_tests, hidden, verify)
    agent_ok = effective_agent.status in {"passed", "external"}
    ok = agent_ok and all(step.ok for step in steps)
    first_pass = (
        ok
        and effective_agent.status == "passed"
        and effective_agent.attempt == 1
        and effective_agent.interventions == 0
    )
    return BenchmarkResult(
        task=task.id,
        task_digest=task.digest,
        ok=ok,
        first_pass=first_pass,
        agent=effective_agent,
        source=source,
        steps=steps,
    )


def summarize_results(results: Sequence[BenchmarkResult]) -> BenchmarkSummary:
    """Aggregate runs without ranking model vendors or exposing prompts."""
    if not results:
        raise BenchmarkError("at least one benchmark result is required")
    grouped: defaultdict[tuple[str, str], list[BenchmarkResult]] = defaultdict(list)
    for result in results:
        validate_benchmark_result(result)
        grouped[(result.task, result.task_digest)].append(result)
    tasks = tuple(
        TaskSummary(
            task=task_key[0],
            task_digest=task_key[1],
            runs=len(items),
            passed=sum(item.ok for item in items),
            first_passed=sum(item.first_pass for item in items),
        )
        for task_key, items in sorted(grouped.items())
    )
    return BenchmarkSummary(
        runs=len(results),
        passed=sum(result.ok for result in results),
        first_passed=sum(result.first_pass for result in results),
        tasks=tasks,
    )


def read_results(paths: Sequence[Path]) -> tuple[BenchmarkResult, ...]:
    results: list[BenchmarkResult] = []
    for path in paths:
        try:
            results.append(parse_result(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError) as exc:
            raise BenchmarkError(f"could not read benchmark result {path}") from exc
    return tuple(results)


def write_payload(
    path: Path,
    value: BenchmarkState | BenchmarkResult | BenchmarkSummary,
) -> None:
    """Atomically write one state, result, or summary outside the agent workspace."""
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = render_payload(value)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary).replace(destination)
    except BaseException:
        with suppress(OSError):
            Path(temporary).unlink()
        raise


def _task_integrity(
    task: BenchmarkTask,
    workspace: Path,
    baseline_commit: str,
) -> StepResult:
    started = time.monotonic()
    try:
        current = (workspace / "TASK.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        current = None
    protected_unchanged = _git_succeeds(
        workspace,
        "diff",
        "--quiet",
        baseline_commit,
        "--",
        *_PROTECTED_PATHS,
    )
    ok = current == task.prompt and protected_unchanged
    return StepResult(
        name="task_integrity",
        status="passed" if ok else "failed",
        exit_code=None,
        duration_seconds=_duration(started),
        code=None if ok else "BENCHMARK_PROTECTED_INPUT_CHANGED",
    )


def _evaluate_agent_tests(
    changed_paths: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> StepResult:
    started = time.monotonic()
    tests = tuple(
        path.endswith(".py")
        and Path(path).name.startswith("test_")
        and "tests" in Path(path).parts
        for path in changed_paths
    )
    test_paths = tuple(
        path for path, is_test in zip(changed_paths, tests, strict=True) if is_test
    )
    if not test_paths:
        return StepResult(
            name="agent_tests",
            status="failed",
            exit_code=None,
            duration_seconds=_duration(started),
            code="BENCHMARK_AGENT_TEST_MISSING",
        )
    if len(test_paths) > _MAX_AGENT_TEST_FILES:
        return StepResult(
            name="agent_tests",
            status="invalid",
            exit_code=None,
            duration_seconds=_duration(started),
            code="BENCHMARK_AGENT_TEST_LIMIT_EXCEEDED",
        )
    process = _run_process(
        _pytest_command(*test_paths),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        capture_stdout=False,
        environment=_evaluator_environment(cwd, disable_pytest_plugins=True),
    )
    if process.timed_out:
        return StepResult(
            "agent_tests",
            "timed_out",
            process.exit_code,
            process.duration_seconds,
            "BENCHMARK_STEP_TIMED_OUT",
        )
    passed = process.exit_code == 0
    return StepResult(
        "agent_tests",
        "passed" if passed else "failed",
        process.exit_code,
        process.duration_seconds,
        None if passed else "BENCHMARK_AGENT_TESTS_FAILED",
    )


def _install_hidden_tests(task: BenchmarkTask, workspace: Path) -> Path:
    tests_root = workspace / "tests"
    if not tests_root.is_dir() or tests_root.is_symlink():
        raise BenchmarkError("benchmark tests directory is missing or unsafe")
    contents: list[str] = []
    for source in task.hidden_tests:
        try:
            contents.append(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise BenchmarkError(f"could not read hidden test {source.name!r}") from exc
    target_root = Path(
        tempfile.mkdtemp(
            prefix=f"tenchi_benchmark_hidden_{task.id}_",
            dir=tests_root,
        )
    )
    try:
        for index, content in enumerate(contents):
            destination = target_root / f"test_{target_root.name}_{index}.py"
            destination.write_text(content, encoding="utf-8")
    except OSError as exc:
        shutil.rmtree(target_root, ignore_errors=True)
        raise BenchmarkError("could not install hidden benchmark tests") from exc
    return target_root


def _evaluate_verify(
    workspace: Path,
    *,
    baseline_commit: str,
    timeout_seconds: float,
) -> StepResult:
    process = _run_process(
        (
            _EVALUATOR_PYTHON,
            "-m",
            "tenchi.cli",
            "verify",
            "--base-ref",
            baseline_commit,
            "--json",
        ),
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        capture_stdout=True,
        environment=_evaluator_environment(workspace),
    )
    if process.timed_out:
        return StepResult(
            "tenchi_verify",
            "timed_out",
            process.exit_code,
            process.duration_seconds,
            "BENCHMARK_STEP_TIMED_OUT",
        )
    receipt_ok = False
    if process.stdout is not None:
        try:
            loaded_receipt: object = json.loads(process.stdout.decode("utf-8"))
            if isinstance(loaded_receipt, dict):
                validated = validate_agent_result(
                    "verify", cast(dict[str, object], loaded_receipt)
                )
                receipt_ok = validated.get("ok") is True
        except (UnicodeError, json.JSONDecodeError, ValueError):
            receipt_ok = False
    ok = process.exit_code == 0 and receipt_ok
    return StepResult(
        "tenchi_verify",
        "passed" if ok else "invalid" if process.exit_code == 0 else "failed",
        process.exit_code,
        process.duration_seconds,
        None if ok else "BENCHMARK_VERIFICATION_FAILED",
    )


def _evaluate_command(
    name: StepName,
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> StepResult:
    process = _run_process(
        command,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        capture_stdout=False,
        environment=_evaluator_environment(cwd, disable_pytest_plugins=True),
    )
    if process.timed_out:
        status: Literal["passed", "failed", "timed_out"] = "timed_out"
        code = "BENCHMARK_STEP_TIMED_OUT"
    elif process.exit_code == 0:
        status = "passed"
        code = None
    else:
        status = "failed"
        code = "BENCHMARK_HIDDEN_TESTS_FAILED"
    return StepResult(name, status, process.exit_code, process.duration_seconds, code)


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    exit_code: int | None
    duration_seconds: float
    timed_out: bool
    stdout: bytes | None


@dataclass(slots=True)
class _BoundedCapture:
    data: bytearray = field(default_factory=bytearray)
    overflowed: bool = False
    failed: bool = False


def _run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    stdin: bytes | None = None,
    capture_stdout: bool,
    environment: Mapping[str, str] | None = None,
    quiet: bool = False,
) -> _ProcessResult:
    if not command:
        raise BenchmarkError("process command must not be empty")
    started = time.monotonic()
    process_environment = (
        os.environ.copy() if environment is None else dict(environment)
    )
    if capture_stdout and stdin is not None:
        raise BenchmarkError("captured benchmark commands cannot receive input")
    try:
        process = subprocess.Popen(
            tuple(command),
            cwd=cwd,
            env=process_environment,
            stdin=subprocess.PIPE if stdin is not None else None,
            stdout=(
                subprocess.PIPE
                if capture_stdout
                else subprocess.DEVNULL
                if quiet
                else None
            ),
            stderr=subprocess.DEVNULL if quiet else None,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise BenchmarkError(f"could not start command {command[0]!r}") from exc
    capture = _BoundedCapture()
    reader: threading.Thread | None = None
    if process.stdout is not None:
        reader = threading.Thread(
            target=_read_bounded_output,
            args=(process.stdout, capture),
            daemon=True,
        )
        reader.start()
    try:
        if capture_stdout:
            process.wait(timeout=timeout_seconds)
        else:
            process.communicate(input=stdin, timeout=timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_process(process)
    except BaseException:
        _stop_process(process)
        raise
    if not timed_out and os.name == "posix":
        _stop_remaining_process_group(process.pid)
    if reader is not None:
        reader.join(timeout=2)
        if reader.is_alive():
            capture.failed = True
            with suppress(OSError):
                cast(BinaryIO, process.stdout).close()
            reader.join(timeout=2)
    encoded = (
        bytes(capture.data)
        if capture_stdout
        and not capture.overflowed
        and not capture.failed
        and (reader is None or not reader.is_alive())
        else None
    )
    return _ProcessResult(
        exit_code=process.returncode,
        duration_seconds=_duration(started),
        timed_out=timed_out,
        stdout=encoded,
    )


def _read_bounded_output(stream: BinaryIO, capture: _BoundedCapture) -> None:
    try:
        while chunk := stream.read(65_536):
            remaining = _MAX_CAPTURE_BYTES - len(capture.data)
            if remaining > 0:
                capture.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                capture.overflowed = True
    except (OSError, ValueError):
        capture.failed = True
    finally:
        with suppress(OSError):
            stream.close()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        if process.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=2)
        return
    if process.poll() is not None:  # pragma: no cover - exercised on Windows CI
        return
    try:  # pragma: no cover - exercised on Windows CI
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - Windows CI
        with suppress(OSError):
            process.kill()
        with suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)


def _stop_remaining_process_group(process_group: int) -> None:
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    time.sleep(0.05)
    with suppress(ProcessLookupError):
        os.killpg(process_group, signal.SIGKILL)


def _source_result(
    workspace: Path, baseline_commit: str
) -> tuple[SourceResult, tuple[str, ...]]:
    head = _git_text(workspace, "rev-parse", "HEAD")
    tracked = _git_paths(
        workspace,
        "diff",
        "--name-only",
        "-z",
        baseline_commit,
    )
    untracked = _git_paths(
        workspace,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    paths = tuple(
        path
        for path in sorted(set((*tracked, *untracked)))
        if not path.startswith(_HIDDEN_TEST_PREFIXES)
    )
    return (
        SourceResult(
            baseline_commit=baseline_commit,
            head_commit=head,
            changed_file_count=len(paths),
            changed_files=paths[:_MAX_REPORTED_PATHS],
        ),
        paths,
    )


def _git_text(cwd: Path, *arguments: str) -> str:
    result = _run_required(
        git_command(*arguments),
        cwd=cwd,
        capture=True,
        environment=git_environment(),
    )
    return result.strip()


def _git_paths(cwd: Path, *arguments: str) -> tuple[str, ...]:
    text = _run_required(
        git_command(*arguments),
        cwd=cwd,
        capture=True,
        environment=git_environment(),
    )
    return tuple(path for path in text.split("\0") if path)


def _run_required(
    command: Sequence[str],
    *,
    cwd: Path,
    capture: bool = False,
    environment: Mapping[str, str] | None = None,
) -> str:
    result = _run_process(
        command,
        cwd=cwd,
        timeout_seconds=_REQUIRED_COMMAND_TIMEOUT_SECONDS,
        capture_stdout=capture,
        environment=environment,
        quiet=True,
    )
    if result.timed_out:
        raise BenchmarkError(f"command {command[0]!r} timed out")
    if result.exit_code != 0:
        raise BenchmarkError(f"command {command[0]!r} failed")
    if not capture:
        return ""
    if result.stdout is None:
        raise BenchmarkError(f"command {command[0]!r} produced too much output")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise BenchmarkError(f"command {command[0]!r} produced invalid output") from exc


def _run_git_required(cwd: Path, *arguments: str) -> None:
    _run_required(
        git_command(*arguments),
        cwd=cwd,
        environment=git_environment(),
    )


def _validate_workspace_baseline(workspace: Path, baseline_commit: str) -> None:
    if not _git_succeeds(
        workspace,
        "cat-file",
        "-e",
        f"{baseline_commit}^{{commit}}",
    ) or not _git_succeeds(
        workspace,
        "merge-base",
        "--is-ancestor",
        baseline_commit,
        "HEAD",
    ):
        raise BenchmarkError("benchmark baseline is not an ancestor of the workspace")


def _git_succeeds(cwd: Path, *arguments: str) -> bool:
    try:
        completed = subprocess.run(
            git_command(*arguments),
            cwd=cwd,
            check=False,
            env=git_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BenchmarkError("could not inspect benchmark Git state") from exc
    return completed.returncode == 0


def _workspace_environment(
    workspace: Path,
    additions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = os.environ.copy()
    virtual_environment = workspace / ".venv"
    command_directory = _virtualenv_command_directory(workspace)
    existing_path = environment.get("PATH", "")
    environment["VIRTUAL_ENV"] = str(virtual_environment)
    environment["PATH"] = (
        f"{command_directory}{os.pathsep}{existing_path}"
        if existing_path
        else str(command_directory)
    )
    if additions is not None:
        environment.update(additions)
    return environment


def _evaluator_environment(
    workspace: Path,
    *,
    disable_pytest_plugins: bool = False,
) -> dict[str, str]:
    """Use the harness interpreter, never agent-writable workspace commands."""
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    workspace_bin = str(_virtualenv_command_directory(workspace))
    executable_directory = str(Path(_EVALUATOR_PYTHON).parent)
    path_entries = environment.get("PATH", "").split(os.pathsep)
    environment["PATH"] = os.pathsep.join(
        [
            executable_directory,
            *(
                entry
                for entry in path_entries
                if entry and entry != workspace_bin and entry != executable_directory
            ),
        ]
    )
    if sys.prefix != sys.base_prefix:
        environment["VIRTUAL_ENV"] = sys.prefix
    else:
        environment.pop("VIRTUAL_ENV", None)
    if disable_pytest_plugins:
        environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return environment


def _pytest_command(*targets: str, confcutdir: Path | None = None) -> tuple[str, ...]:
    command = [
        _EVALUATOR_PYTHON,
        "-m",
        "pytest",
        "-c",
        str(_PYTEST_CONFIG),
        "--rootdir=.",
        "-p",
        "pytest_asyncio.plugin",
        "--capture=no",
        "-q",
    ]
    if confcutdir is not None:
        command.append(f"--confcutdir={confcutdir}")
    command.extend(targets)
    return tuple(command)


def _virtualenv_command_directory(workspace: Path) -> Path:
    return workspace / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def _local_project(source: str, tenchi_root: Path) -> str:
    uri = tenchi_root.as_uri()
    rendered = source.replace('"tenchi"', f'"tenchi @ {uri}"').replace(
        '"tenchi[mcp]"', f'"tenchi[mcp] @ {uri}"'
    )
    if rendered == source:
        raise BenchmarkError("generated pyproject did not contain Tenchi dependencies")
    return rendered


def _task_path(root: Path, relative: str, *, label: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BenchmarkError(f"benchmark {label} must stay inside its task") from exc
    return path


def _task_digest(root: Path, paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    benchmark_inputs = _benchmark_input_paths()
    inputs = (
        *(("benchmark", _REPOSITORY_ROOT, path) for path in benchmark_inputs),
        *(("task", root, path) for path in paths),
    )
    for namespace, base, path in inputs:
        try:
            relative = f"{namespace}/{path.relative_to(base).as_posix()}".encode()
            content = path.read_bytes()
        except (OSError, UnicodeError, ValueError) as exc:
            raise BenchmarkError("could not fingerprint benchmark task") from exc
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return f"sha256:{digest.hexdigest()}"


def _benchmark_input_paths() -> tuple[Path, ...]:
    framework = tuple(sorted((_REPOSITORY_ROOT / "src/tenchi").rglob("*.py")))
    harness = tuple(sorted(_BENCHMARK_ROOT.glob("*.py")))
    return (
        _REPOSITORY_ROOT / "pyproject.toml",
        _REPOSITORY_ROOT / "uv.lock",
        *framework,
        *harness,
        _PYTEST_CONFIG,
    )


def _required_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"benchmark {label} must be a non-empty string")
    return value.strip()


def _positive_seconds(value: object, *, label: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise BenchmarkError(f"benchmark {label} must be a number")
    numeric = float(value)
    if not 0 < numeric <= 31_536_000:
        raise BenchmarkError(f"benchmark {label} must be finite and positive")
    return numeric


def _positive_int(value: object, *, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 10_000
    ):
        raise BenchmarkError(f"benchmark {label} must be a positive integer")
    return value


def _non_negative_int(value: object, *, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 9_007_199_254_740_991
    ):
        raise BenchmarkError(f"benchmark {label} must be a non-negative integer")
    return value


def _valid_task_id(value: str) -> bool:
    return (
        bool(value)
        and value[0].islower()
        and all(character in _TASK_ID_CHARS for character in value)
    )


def _validate_commit(value: str) -> None:
    if len(value) not in _COMMIT_LENGTHS or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise BenchmarkError("benchmark baseline is not an immutable Git commit")


def _duration(started: float) -> float:
    return max(0.0, round(time.monotonic() - started, 6))
