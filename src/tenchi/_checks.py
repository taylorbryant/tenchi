"""Run the complete, agent-readable validation loop for a Tenchi app."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryFile
from time import perf_counter, sleep
from typing import BinaryIO

from ._cli_results import CheckResult, CheckStepResult

_MAX_OUTPUT_BYTES = 65_536
_POLL_SECONDS = 0.05


class CheckCancelled(Exception):
    """Raised after the active check subprocess has been stopped."""


@dataclass(frozen=True, slots=True)
class _CheckCommand:
    name: str
    command: tuple[str, ...]


def run_check(
    root: Path,
    *,
    routes: str,
    title: str,
    version: str,
    description: str | None,
    snapshot: str,
    security_json: str | None,
    timeout_seconds: float,
    cancelled: Callable[[], bool] | None = None,
    step_completed: Callable[[int, int, CheckStepResult], None] | None = None,
) -> CheckResult:
    """Run every validation step and retain bounded output for failures."""
    resolved_root = root.resolve()
    if not (resolved_root / "app").is_dir():
        return CheckResult(
            root=str(resolved_root),
            ok=False,
            steps=(),
            duration_seconds=0.0,
            error="app/ not found; run this from an application root",
        )
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        return CheckResult(
            root=str(resolved_root),
            ok=False,
            steps=(),
            duration_seconds=0.0,
            error="timeout_seconds must be a finite number greater than zero",
        )

    started = perf_counter()
    commands = _check_commands(
        routes=routes,
        title=title,
        version=version,
        description=description,
        snapshot=snapshot,
        security_json=security_json,
    )
    results: list[CheckStepResult] = []
    for index, command in enumerate(commands, start=1):
        if cancelled is not None and cancelled():
            raise CheckCancelled
        result = _run_step(
            command,
            root=resolved_root,
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )
        results.append(result)
        if step_completed is not None:
            step_completed(index, len(commands), result)
    return CheckResult(
        root=str(resolved_root),
        ok=all(step.status == "passed" for step in results),
        steps=tuple(results),
        duration_seconds=_seconds_since(started),
    )


def _check_commands(
    *,
    routes: str,
    title: str,
    version: str,
    description: str | None,
    snapshot: str,
    security_json: str | None,
) -> tuple[_CheckCommand, ...]:
    openapi = [
        "tenchi",
        "openapi",
        "--routes",
        routes,
        "--title",
        title,
        "--version",
        version,
    ]
    if description is not None:
        openapi.extend(("--description", description))
    if security_json is not None:
        openapi.extend(("--security", security_json))
    openapi.extend(("--check", snapshot))
    return (
        _CheckCommand("ruff format", ("ruff", "format", "--check", ".")),
        _CheckCommand("ruff", ("ruff", "check", ".")),
        _CheckCommand("pyright", ("pyright",)),
        _CheckCommand("pytest", ("pytest",)),
        _CheckCommand("doctor", ("tenchi", "doctor")),
        _CheckCommand("openapi", tuple(openapi)),
    )


def _run_step(
    step: _CheckCommand,
    *,
    root: Path,
    timeout_seconds: float,
    cancelled: Callable[[], bool] | None,
) -> CheckStepResult:
    started = perf_counter()
    try:
        with (
            TemporaryFile(mode="w+b") as stdout_file,
            TemporaryFile(mode="w+b") as stderr_file,
        ):
            process = _start_process(
                _execution_command(step.command),
                root=root,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
            deadline = perf_counter() + timeout_seconds
            timed_out = False
            while process.poll() is None:
                if cancelled is not None and cancelled():
                    _stop_process(process)
                    raise CheckCancelled
                if perf_counter() >= deadline:
                    timed_out = True
                    _stop_process(process)
                    break
                sleep(_POLL_SECONDS)

            return_code = (
                process.wait() if process.returncode is None else process.returncode
            )
            if timed_out:
                stdout, stdout_truncated = _read_output_tail(stdout_file)
                stderr, stderr_truncated = _read_output_tail(stderr_file)
                timeout_message = f"timed out after {timeout_seconds:g} seconds"
                raw_stderr = stderr.rstrip()
                raw_stderr = (
                    f"{raw_stderr}\n{timeout_message}"
                    if raw_stderr
                    else timeout_message
                )
                stderr, message_truncated = _bounded_output(raw_stderr)
                return CheckStepResult(
                    name=step.name,
                    command=step.command,
                    status="failed",
                    exit_code=124,
                    duration_seconds=_seconds_since(started),
                    stdout=stdout,
                    stderr=stderr,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated or message_truncated,
                )

            stdout, stdout_truncated = _read_output_tail(stdout_file)
            stderr, stderr_truncated = _read_output_tail(stderr_file)
            return CheckStepResult(
                name=step.name,
                command=step.command,
                status="passed" if return_code == 0 else "failed",
                exit_code=return_code,
                duration_seconds=_seconds_since(started),
                stdout="" if return_code == 0 else stdout,
                stderr="" if return_code == 0 else stderr,
                stdout_truncated=(stdout_truncated if return_code != 0 else False),
                stderr_truncated=(stderr_truncated if return_code != 0 else False),
            )
    except OSError as exc:
        stderr, stderr_truncated = _bounded_output(str(exc))
        return CheckStepResult(
            name=step.name,
            command=step.command,
            status="failed",
            exit_code=127,
            duration_seconds=_seconds_since(started),
            stdout="",
            stderr=stderr,
            stdout_truncated=False,
            stderr_truncated=stderr_truncated,
        )


def _start_process(
    command: list[str],
    *,
    root: Path,
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
) -> subprocess.Popen[bytes]:
    if os.name == "posix":
        return subprocess.Popen(
            command,
            cwd=root,
            stdout=stdout_file,
            stderr=stderr_file,
            env=_child_environment(),
            start_new_session=True,
        )
    return subprocess.Popen(
        command,
        cwd=root,
        stdout=stdout_file,
        stderr=stderr_file,
        env=_child_environment(),
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            process.wait()
            return
    else:  # pragma: no cover - exercised on Windows CI when available
        process.terminate()
    try:
        process.wait(timeout=1)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.wait()
            return
    else:  # pragma: no cover - exercised on Windows CI when available
        process.kill()
    process.wait()


def _execution_command(command: tuple[str, ...]) -> list[str]:
    module = "tenchi.cli" if command[0] == "tenchi" else command[0]
    return [sys.executable, "-m", module, *command[1:]]


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    executable_directory = str(Path(sys.executable).parent)
    path_entries = environment.get("PATH", "").split(os.pathsep)
    environment["PATH"] = os.pathsep.join(
        [executable_directory, *(entry for entry in path_entries if entry)]
    )
    if sys.prefix != sys.base_prefix:
        environment["VIRTUAL_ENV"] = sys.prefix
    return environment


def _read_output_tail(stream: BinaryIO) -> tuple[str, bool]:
    stream.flush()
    size = stream.seek(0, os.SEEK_END)
    truncated = size > _MAX_OUTPUT_BYTES
    stream.seek(max(0, size - _MAX_OUTPUT_BYTES))
    value = stream.read(_MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")
    bounded, decode_truncated = _bounded_output(value)
    return bounded, truncated or decode_truncated


def _bounded_output(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    value = encoded.decode("utf-8")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return value, False
    bounded = encoded[-_MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace")
    while len(bounded.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        bounded = bounded[1:]
    return bounded, True


def _seconds_since(started: float) -> float:
    return round(perf_counter() - started, 6)
