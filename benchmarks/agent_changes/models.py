"""Versioned, payload-safe models for coding-agent benchmark results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict

BENCHMARK_PROTOCOL_VERSION = 1
_MAX_COUNT = 9_007_199_254_740_991
_Count = Annotated[int, Field(ge=0, le=_MAX_COUNT)]
_Duration = Annotated[float, Field(ge=0, le=31_536_000)]
_Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_NonEmpty = Annotated[str, Field(min_length=1)]

type AgentStatus = Literal["passed", "failed", "timed_out", "external"]
type StepStatus = Literal["passed", "failed", "timed_out", "invalid"]
type StepName = Literal[
    "task_integrity",
    "agent_tests",
    "hidden_acceptance",
    "tenchi_verify",
]
type AgentInterface = Literal["cli", "mcp", "mixed", "unknown"]


@dataclass(frozen=True, slots=True)
class BenchmarkTask:
    """One repository-owned task and its hidden evaluator inputs."""

    id: str
    digest: str
    title: str
    description: str
    prompt: str
    prompt_path: Path
    hidden_tests: tuple[Path, ...]
    agent_timeout_seconds: float
    evaluation_timeout_seconds: float


@with_config(ConfigDict(extra="forbid"))
class ListedTaskPayload(TypedDict):
    id: _NonEmpty
    digest: _Digest
    title: _NonEmpty
    description: _NonEmpty


@with_config(ConfigDict(extra="forbid"))
class BenchmarkTaskListPayload(TypedDict):
    schema_version: Literal[1]
    tasks: list[ListedTaskPayload]


@dataclass(frozen=True, slots=True)
class BenchmarkTaskList:
    tasks: tuple[BenchmarkTask, ...]
    schema_version: Literal[1] = BENCHMARK_PROTOCOL_VERSION

    def as_dict(self) -> BenchmarkTaskListPayload:
        return {
            "schema_version": self.schema_version,
            "tasks": [
                {
                    "id": task.id,
                    "digest": task.digest,
                    "title": task.title,
                    "description": task.description,
                }
                for task in self.tasks
            ],
        }


@with_config(ConfigDict(extra="forbid"))
class BenchmarkStatePayload(TypedDict):
    schema_version: Literal[1]
    task: _NonEmpty
    task_digest: _Digest
    workspace: _NonEmpty
    baseline_commit: _NonEmpty


@dataclass(frozen=True, slots=True)
class BenchmarkState:
    """External state needed to evaluate an already completed agent run."""

    task: str
    task_digest: str
    workspace: str
    baseline_commit: str
    schema_version: Literal[1] = BENCHMARK_PROTOCOL_VERSION

    def as_dict(self) -> BenchmarkStatePayload:
        return {
            "schema_version": self.schema_version,
            "task": self.task,
            "task_digest": self.task_digest,
            "workspace": self.workspace,
            "baseline_commit": self.baseline_commit,
        }


@with_config(ConfigDict(extra="forbid"))
class AgentResultPayload(TypedDict):
    label: _NonEmpty
    interface: AgentInterface
    attempt: Annotated[int, Field(ge=1, le=10_000)]
    interventions: _Count
    status: AgentStatus
    exit_code: int | None
    duration_seconds: _Duration


@dataclass(frozen=True, slots=True)
class AgentResult:
    label: str
    interface: AgentInterface
    attempt: int
    interventions: int
    status: AgentStatus
    exit_code: int | None
    duration_seconds: float

    def as_dict(self) -> AgentResultPayload:
        return {
            "label": self.label,
            "interface": self.interface,
            "attempt": self.attempt,
            "interventions": self.interventions,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
        }


@with_config(ConfigDict(extra="forbid"))
class StepResultPayload(TypedDict):
    name: StepName
    status: StepStatus
    exit_code: int | None
    duration_seconds: _Duration
    code: str | None


@dataclass(frozen=True, slots=True)
class StepResult:
    """One evaluator outcome without command output or application payloads."""

    name: StepName
    status: StepStatus
    exit_code: int | None
    duration_seconds: float
    code: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> StepResultPayload:
        return {
            "name": self.name,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "code": self.code,
        }


@with_config(ConfigDict(extra="forbid"))
class SourceResultPayload(TypedDict):
    baseline_commit: _NonEmpty
    head_commit: _NonEmpty
    changed_file_count: _Count
    changed_files: Annotated[list[_NonEmpty], Field(max_length=200)]


@dataclass(frozen=True, slots=True)
class SourceResult:
    baseline_commit: str
    head_commit: str
    changed_file_count: int
    changed_files: tuple[str, ...]

    def as_dict(self) -> SourceResultPayload:
        return {
            "baseline_commit": self.baseline_commit,
            "head_commit": self.head_commit,
            "changed_file_count": self.changed_file_count,
            "changed_files": list(self.changed_files),
        }


@with_config(ConfigDict(extra="forbid"))
class BenchmarkResultPayload(TypedDict):
    schema_version: Literal[1]
    task: _NonEmpty
    task_digest: _Digest
    ok: bool
    first_pass: bool
    agent: AgentResultPayload
    source: SourceResultPayload
    steps: Annotated[list[StepResultPayload], Field(min_length=4, max_length=4)]


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    task: str
    task_digest: str
    ok: bool
    first_pass: bool
    agent: AgentResult
    source: SourceResult
    steps: tuple[StepResult, ...]
    schema_version: Literal[1] = BENCHMARK_PROTOCOL_VERSION

    def as_dict(self) -> BenchmarkResultPayload:
        return {
            "schema_version": self.schema_version,
            "task": self.task,
            "task_digest": self.task_digest,
            "ok": self.ok,
            "first_pass": self.first_pass,
            "agent": self.agent.as_dict(),
            "source": self.source.as_dict(),
            "steps": [step.as_dict() for step in self.steps],
        }


@with_config(ConfigDict(extra="forbid"))
class TaskSummaryPayload(TypedDict):
    task: _NonEmpty
    task_digest: _Digest
    runs: _Count
    passed: _Count
    first_passed: _Count
    pass_rate: Annotated[float, Field(ge=0, le=1)]
    first_pass_rate: Annotated[float, Field(ge=0, le=1)]


@with_config(ConfigDict(extra="forbid"))
class BenchmarkSummaryPayload(TypedDict):
    schema_version: Literal[1]
    runs: _Count
    passed: _Count
    first_passed: _Count
    pass_rate: Annotated[float, Field(ge=0, le=1)]
    first_pass_rate: Annotated[float, Field(ge=0, le=1)]
    tasks: list[TaskSummaryPayload]


@dataclass(frozen=True, slots=True)
class TaskSummary:
    task: str
    task_digest: str
    runs: int
    passed: int
    first_passed: int

    def as_dict(self) -> TaskSummaryPayload:
        return {
            "task": self.task,
            "task_digest": self.task_digest,
            "runs": self.runs,
            "passed": self.passed,
            "first_passed": self.first_passed,
            "pass_rate": _rate(self.passed, self.runs),
            "first_pass_rate": _rate(self.first_passed, self.runs),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    runs: int
    passed: int
    first_passed: int
    tasks: tuple[TaskSummary, ...]
    schema_version: Literal[1] = BENCHMARK_PROTOCOL_VERSION

    def as_dict(self) -> BenchmarkSummaryPayload:
        return {
            "schema_version": self.schema_version,
            "runs": self.runs,
            "passed": self.passed,
            "first_passed": self.first_passed,
            "pass_rate": _rate(self.passed, self.runs),
            "first_pass_rate": _rate(self.first_passed, self.runs),
            "tasks": [task.as_dict() for task in self.tasks],
        }


_STATE_ADAPTER = TypeAdapter(BenchmarkStatePayload)
_AGENT_RESULT_ADAPTER = TypeAdapter(AgentResultPayload)
_RESULT_ADAPTER = TypeAdapter(BenchmarkResultPayload)
_SUMMARY_ADAPTER = TypeAdapter(BenchmarkSummaryPayload)
_TASK_LIST_ADAPTER = TypeAdapter(BenchmarkTaskListPayload)


def render_payload(
    value: BenchmarkTaskList | BenchmarkState | BenchmarkResult | BenchmarkSummary,
) -> str:
    """Render one canonical, interoperable JSON payload."""
    raw = value.as_dict()
    adapter = (
        _TASK_LIST_ADAPTER
        if isinstance(value, BenchmarkTaskList)
        else _STATE_ADAPTER
        if isinstance(value, BenchmarkState)
        else _RESULT_ADAPTER
        if isinstance(value, BenchmarkResult)
        else _SUMMARY_ADAPTER
    )
    try:
        validated = adapter.validate_python(raw, strict=True)
    except ValidationError as exc:
        raise ValueError("benchmark payload does not match schema version 1") from exc
    if validated != raw:
        raise ValueError("benchmark payload contains non-canonical values")
    if isinstance(value, BenchmarkResult):
        _validate_result_semantics(value)
    elif isinstance(value, BenchmarkSummary):
        _validate_summary_semantics(value)
    return (
        json.dumps(
            validated,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def benchmark_protocol_schemas() -> dict[str, dict[str, object]]:
    """Return every JSON shape governed by the benchmark protocol version."""
    adapters = {
        "list": _TASK_LIST_ADAPTER,
        "state": _STATE_ADAPTER,
        "result": _RESULT_ADAPTER,
        "summary": _SUMMARY_ADAPTER,
    }
    return {
        name: cast(dict[str, object], adapter.json_schema(mode="serialization"))
        for name, adapter in adapters.items()
    }


def parse_state(text: str) -> BenchmarkState:
    try:
        raw: object = json.loads(text)
        payload = _STATE_ADAPTER.validate_python(raw, strict=True)
    except (json.JSONDecodeError, UnicodeError, ValidationError) as exc:
        raise ValueError("benchmark state does not match schema version 1") from exc
    if payload != raw:
        raise ValueError("benchmark state contains non-canonical values")
    return BenchmarkState(
        task=payload["task"],
        task_digest=payload["task_digest"],
        workspace=payload["workspace"],
        baseline_commit=payload["baseline_commit"],
    )


def parse_result(text: str) -> BenchmarkResult:
    try:
        raw: object = json.loads(text)
        payload = _RESULT_ADAPTER.validate_python(raw, strict=True)
    except (json.JSONDecodeError, UnicodeError, ValidationError) as exc:
        raise ValueError("benchmark result does not match schema version 1") from exc
    if payload != raw:
        raise ValueError("benchmark result contains non-canonical values")
    agent = payload["agent"]
    source = payload["source"]
    result = BenchmarkResult(
        task=payload["task"],
        task_digest=payload["task_digest"],
        ok=payload["ok"],
        first_pass=payload["first_pass"],
        agent=AgentResult(
            label=agent["label"],
            interface=agent["interface"],
            attempt=agent["attempt"],
            interventions=agent["interventions"],
            status=agent["status"],
            exit_code=agent["exit_code"],
            duration_seconds=agent["duration_seconds"],
        ),
        source=SourceResult(
            baseline_commit=source["baseline_commit"],
            head_commit=source["head_commit"],
            changed_file_count=source["changed_file_count"],
            changed_files=tuple(source["changed_files"]),
        ),
        steps=tuple(
            StepResult(
                name=step["name"],
                status=step["status"],
                exit_code=step["exit_code"],
                duration_seconds=step["duration_seconds"],
                code=step["code"],
            )
            for step in payload["steps"]
        ),
    )
    _validate_result_semantics(result)
    return result


def validate_agent_result(value: AgentResult) -> AgentResult:
    """Reject inconsistent externally supplied agent metadata."""
    raw = value.as_dict()
    try:
        payload = _AGENT_RESULT_ADAPTER.validate_python(raw, strict=True)
    except ValidationError as exc:
        raise ValueError("benchmark agent metadata is invalid") from exc
    if payload != raw or not payload["label"].strip():
        raise ValueError("benchmark agent metadata is invalid")
    exit_code = payload["exit_code"]
    status = payload["status"]
    if status == "passed" and exit_code != 0:
        raise ValueError("a passed benchmark agent must have exit code zero")
    if status == "failed" and (exit_code is None or exit_code == 0):
        raise ValueError("a failed benchmark agent must have a failing exit code")
    if status == "external" and exit_code is not None:
        raise ValueError("an external benchmark agent cannot claim an exit code")
    return value


def validate_benchmark_result(value: BenchmarkResult) -> BenchmarkResult:
    """Reject a shaped result whose outcome claims contradict its evidence."""
    raw = value.as_dict()
    try:
        payload = _RESULT_ADAPTER.validate_python(raw, strict=True)
    except ValidationError as exc:
        raise ValueError("benchmark result does not match schema version 1") from exc
    if payload != raw:
        raise ValueError("benchmark result contains non-canonical values")
    _validate_result_semantics(value)
    return value


def _validate_result_semantics(result: BenchmarkResult) -> None:
    validate_agent_result(result.agent)
    expected_steps = (
        "task_integrity",
        "agent_tests",
        "hidden_acceptance",
        "tenchi_verify",
    )
    if tuple(step.name for step in result.steps) != expected_steps:
        raise ValueError("benchmark result has invalid evaluator steps")
    expected_ok = result.agent.status in {"passed", "external"} and all(
        step.ok for step in result.steps
    )
    expected_first_pass = (
        expected_ok
        and result.agent.status == "passed"
        and result.agent.attempt == 1
        and result.agent.interventions == 0
    )
    if result.ok != expected_ok or result.first_pass != expected_first_pass:
        raise ValueError("benchmark result has inconsistent outcome flags")
    paths = result.source.changed_files
    if len(paths) > result.source.changed_file_count or paths != tuple(
        sorted(set(paths))
    ):
        raise ValueError("benchmark result has inconsistent changed paths")


def _validate_summary_semantics(summary: BenchmarkSummary) -> None:
    keys = tuple((task.task, task.task_digest) for task in summary.tasks)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("benchmark summary has inconsistent task groups")
    if any(
        task.runs < 1 or task.passed > task.runs or task.first_passed > task.passed
        for task in summary.tasks
    ):
        raise ValueError("benchmark summary has inconsistent task counts")
    if (
        summary.runs != sum(task.runs for task in summary.tasks)
        or summary.passed != sum(task.passed for task in summary.tasks)
        or summary.first_passed != sum(task.first_passed for task in summary.tasks)
    ):
        raise ValueError("benchmark summary has inconsistent totals")


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def validated_interface(value: str) -> AgentInterface:
    if value not in {"cli", "mcp", "mixed", "unknown"}:
        raise ValueError(f"unsupported benchmark interface {value!r}")
    return cast(AgentInterface, value)
