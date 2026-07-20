"""Run the complete, agent-readable validation loop for a Tenchi app."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from tempfile import TemporaryFile
from time import perf_counter
from typing import BinaryIO

from ._cli_results import CheckResult, CheckStepResult

_MAX_OUTPUT_BYTES = 65_536


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
    results = tuple(
        _run_step(command, root=resolved_root, timeout_seconds=timeout_seconds)
        for command in _check_commands(
            routes=routes,
            title=title,
            version=version,
            description=description,
            snapshot=snapshot,
            security_json=security_json,
        )
    )
    return CheckResult(
        root=str(resolved_root),
        ok=all(step.status == "passed" for step in results),
        steps=results,
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
    step: _CheckCommand, *, root: Path, timeout_seconds: float
) -> CheckStepResult:
    started = perf_counter()
    try:
        with (
            TemporaryFile(mode="w+b") as stdout_file,
            TemporaryFile(mode="w+b") as stderr_file,
        ):
            try:
                completed = subprocess.run(
                    _execution_command(step.command),
                    cwd=root,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    check=False,
                    timeout=timeout_seconds,
                    env=_child_environment(),
                )
            except subprocess.TimeoutExpired:
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
                status="passed" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
                duration_seconds=_seconds_since(started),
                stdout="" if completed.returncode == 0 else stdout,
                stderr="" if completed.returncode == 0 else stderr,
                stdout_truncated=(
                    stdout_truncated if completed.returncode != 0 else False
                ),
                stderr_truncated=(
                    stderr_truncated if completed.returncode != 0 else False
                ),
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
