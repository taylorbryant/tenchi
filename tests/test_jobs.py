"""Validated background-job declarations, messages, and dispatch."""

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import Annotated, cast

import pytest
from pydantic import BaseModel, PlainSerializer, TypeAdapter, ValidationError

from tenchi._schema_compatibility import ChangeSeverity, compare_schema
from tenchi.compatibility import analyze_job_compatibility
from tenchi.errors import ConfigurationError
from tenchi.execution import UseCaseOutcome
from tenchi.jobs import (
    JOB_MANIFEST_VERSION,
    Job,
    JobBindingError,
    JobDispatcher,
    JobGroup,
    JobHandler,
    JobManifest,
    JobNotFoundError,
    JobResultError,
    create_job_dispatcher,
    job,
    job_group,
    job_handler,
    job_manifest,
    job_message,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
JOB_MANIFEST_SNAPSHOT_PATH = (
    Path(__file__).parent
    / f"job_manifest_protocol_v{JOB_MANIFEST_VERSION}_snapshot.json"
)
UPDATE_JOB_MANIFEST_SNAPSHOT = "TENCHI_UPDATE_JOB_MANIFEST_SNAPSHOT"
JOB_MANIFEST_BASE_REF = "TENCHI_JOB_MANIFEST_BASE_REF"


class Delivery(BaseModel):
    message_id: str
    address: str


class DeliveryResult(BaseModel):
    accepted: bool


@dataclass(frozen=True, slots=True)
class Context:
    events: list[str]


delivery_job = job(
    "mail.deliver",
    request=Delivery,
    result=DeliveryResult,
    description="Deliver one queued message.",
)

SerializedInteger = Annotated[
    int,
    PlainSerializer(lambda value: f"private-{value}", return_type=str),
]


async def deliver(request: Delivery, context: Context) -> DeliveryResult:
    context.events.append(f"delivered:{request.message_id}")
    return DeliveryResult(accepted=True)


def test_job_message_validates_and_serializes_without_repr_payload() -> None:
    message = job_message(
        delivery_job,
        Delivery(message_id="m1", address="user@example.com"),
    )

    assert message.name == "mail.deliver"
    assert message.payload_json == (b'{"message_id":"m1","address":"user@example.com"}')
    assert "user@example.com" not in repr(message)
    with pytest.raises(FrozenInstanceError):
        cast(object, message).name = "changed"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        job_message(
            delivery_job,
            {"message_id": "m1"},  # pyright: ignore[reportArgumentType]
        )


def test_job_message_rejects_json_the_consumer_cannot_read() -> None:
    floating_point_job = job("math.record", request=float, result=None)

    with pytest.raises(JobBindingError, match="published input schema") as excinfo:
        job_message(floating_point_job, float("inf"))

    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_job_message_rejects_serializers_that_violate_the_manifest_schema() -> None:
    serialized_job = cast(
        Job[int, None],
        job(  # pyright: ignore[reportCallIssue, reportArgumentType]
            "math.record",
            request=SerializedInteger,  # pyright: ignore[reportArgumentType]
            result=None,
        ),
    )

    with pytest.raises(JobBindingError, match="published input schema") as excinfo:
        job_message(serialized_job, 1)

    assert "private-1" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_job_manifest_is_canonical_and_contains_only_message_contracts() -> None:
    second_job = job(
        "system.refresh",
        request=str,
        result=int,
        description=None,
    )

    async def refresh(request: str, context: Context) -> int:
        return len(request)

    delivery_binding = job_handler(delivery_job, deliver)
    refresh_binding = job_handler(second_job, refresh)
    manifest = job_manifest(job_group(refresh_binding, delivery_binding))
    reversed_manifest = job_manifest(job_group(delivery_binding, refresh_binding))

    assert manifest == reversed_manifest
    assert manifest["schema_version"] == JOB_MANIFEST_VERSION
    assert [item["name"] for item in manifest["jobs"]] == [
        "mail.deliver",
        "system.refresh",
    ]
    assert manifest["jobs"][0]["input_schema"]["type"] == "object"
    assert manifest["jobs"][1] == {
        "name": "system.refresh",
        "description": None,
        "input_schema": {"type": "string"},
    }
    assert "result_schema" not in manifest["jobs"][0]
    assert json.loads(json.dumps(manifest, sort_keys=True)) == manifest


def test_job_manifest_requires_a_job_group() -> None:
    with pytest.raises(ConfigurationError, match="must be a JobGroup"):
        job_manifest(cast(object, ()))  # pyright: ignore[reportArgumentType]


def test_direct_job_construction_preserves_declaration_invariants() -> None:
    with pytest.raises(JobBindingError, match="dotted snake_case"):
        Job(
            name="Invalid Job",
            description=None,
            request=Delivery,
            result=DeliveryResult,
        )
    with pytest.raises(JobBindingError, match="non-empty string"):
        Job(
            name="mail.deliver",
            description=" ",
            request=Delivery,
            result=DeliveryResult,
        )

    declaration: Job[Delivery, DeliveryResult] = Job(
        name="mail.deliver",
        description="  Deliver one message.  ",
        request=Delivery,
        result=DeliveryResult,
    )

    assert declaration.description == "Deliver one message."


def test_job_compatibility_is_directional_for_durable_inputs() -> None:
    baseline = {
        "schema_version": 1,
        "jobs": [
            {
                "name": "mail.deliver",
                "description": "Deliver.",
                "input_schema": {
                    "type": "object",
                    "properties": {"message_id": {"type": "string"}},
                    "required": ["message_id"],
                },
            }
        ],
    }
    additive = json.loads(json.dumps(baseline))
    cast(dict[str, object], additive["jobs"][0]["input_schema"])["properties"] = {
        "message_id": {"type": "string"},
        "priority": {"type": "integer"},
    }
    breaking = json.loads(json.dumps(additive))
    cast(dict[str, object], breaking["jobs"][0]["input_schema"])["required"] = [
        "message_id",
        "priority",
    ]

    additive_report = analyze_job_compatibility(baseline, additive)
    breaking_report = analyze_job_compatibility(additive, breaking)

    assert additive_report.compatible is True
    assert any(change.severity == "additive" for change in additive_report.changes)
    assert breaking_report.compatible is False
    assert any(change.severity == "breaking" for change in breaking_report.changes)


def test_job_compatibility_classifies_names_and_descriptions() -> None:
    baseline = job_manifest(job_group(job_handler(delivery_job, deliver)))
    current = json.loads(json.dumps(baseline))
    current["jobs"][0]["description"] = "Updated documentation."
    current["jobs"].append(
        {
            "name": "mail.retry",
            "description": None,
            "input_schema": {"type": "string"},
        }
    )

    report = analyze_job_compatibility(baseline, current)

    assert report.compatible is True
    assert report.count("metadata") == 1
    assert report.count("additive") == 1

    removed: dict[str, object] = {**baseline, "jobs": []}
    assert analyze_job_compatibility(baseline, removed).compatible is False


def test_job_handler_validates_exact_annotations_and_shape() -> None:
    async def wrong_request(request: str, context: Context) -> DeliveryResult:
        return DeliveryResult(accepted=True)

    async def wrong_result(request: Delivery, context: Context) -> str:
        return "yes"

    def sync_handler(request: Delivery, context: Context) -> DeliveryResult:
        return DeliveryResult(accepted=True)

    with pytest.raises(JobBindingError, match="request annotation"):
        job_handler(delivery_job, wrong_request)  # pyright: ignore[reportArgumentType]
    with pytest.raises(JobBindingError, match="return annotation"):
        job_handler(delivery_job, wrong_result)  # pyright: ignore[reportArgumentType]
    with pytest.raises(JobBindingError, match="must be an async function"):
        job_handler(delivery_job, sync_handler)  # pyright: ignore[reportArgumentType]


async def test_dispatch_validates_input_and_result_inside_context_scope() -> None:
    events: list[str] = []
    observed: list[UseCaseOutcome] = []

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(events)
        finally:
            events.append("exit")

    def observe(outcome: UseCaseOutcome) -> None:
        events.append("observe")
        observed.append(outcome)

    dispatcher = create_job_dispatcher(
        jobs=job_group(job_handler(delivery_job, deliver)),
        use_case_observers=(observe,),
    )
    result = await dispatcher.dispatch(
        "mail.deliver",
        payload_json=job_message(
            delivery_job,
            Delivery(message_id="m1", address="user@example.com"),
        ).payload_json,
        context=context,
    )

    assert result == DeliveryResult(accepted=True)
    assert events == ["enter", "delivered:m1", "exit", "observe"]
    assert len(observed) == 1
    assert observed[0].entrypoint == "job"
    assert observed[0].status == "succeeded"

    with pytest.raises(ValidationError):
        await dispatcher.dispatch(
            "mail.deliver",
            payload_json=b'{"message_id":"missing-address"}',
            context=context,
        )
    assert events == ["enter", "delivered:m1", "exit", "observe"]


async def test_invalid_result_reaches_context_exit_before_raising() -> None:
    events: list[str] = []

    async def invalid(request: Delivery, context: Context) -> DeliveryResult:
        context.events.append("handler")
        return cast(DeliveryResult, {"accepted": "not-a-bool"})

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        try:
            yield Context(events)
        except JobResultError:
            events.append("rollback")
            raise

    dispatcher = create_job_dispatcher(
        jobs=job_group(job_handler(delivery_job, invalid))
    )
    with pytest.raises(JobResultError):
        await dispatcher.dispatch(
            "mail.deliver",
            payload_json=b'{"message_id":"m1","address":"user@example.com"}',
            context=context,
        )
    assert events == ["handler", "rollback"]


async def test_unknown_job_never_opens_context() -> None:
    opened = False

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        nonlocal opened
        opened = True
        yield Context([])

    dispatcher = create_job_dispatcher(jobs=job_group())
    with pytest.raises(JobNotFoundError, match="unknown job"):
        await dispatcher.dispatch(
            "mail.unknown",
            payload_json=b"{}",
            context=context,
        )
    assert opened is False


async def test_dispatch_cancellation_closes_context_and_notifies() -> None:
    entered = asyncio.Event()
    exited = asyncio.Event()
    observed: list[UseCaseOutcome] = []

    async def wait(request: Delivery, context: Context) -> DeliveryResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def context() -> AsyncGenerator[Context]:
        try:
            yield Context([])
        finally:
            exited.set()

    dispatcher = create_job_dispatcher(
        jobs=job_group(job_handler(delivery_job, wait)),
        use_case_observers=(observed.append,),
    )
    running = asyncio.create_task(
        dispatcher.dispatch(
            "mail.deliver",
            payload_json=b'{"message_id":"m1","address":"user@example.com"}',
            context=context,
        )
    )
    await entered.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert exited.is_set()
    assert len(observed) == 1
    assert observed[0].entrypoint == "job"
    assert observed[0].status == "cancelled"


def test_job_groups_reject_duplicate_names_and_invalid_composition() -> None:
    handler = job_handler(delivery_job, deliver)
    with pytest.raises(ConfigurationError, match="duplicate job name"):
        job_group(handler, handler)
    with pytest.raises(ConfigurationError, match="must be a JobHandler"):
        job_group(cast(object, delivery_job))  # pyright: ignore[reportArgumentType]


def test_direct_job_binding_and_group_construction_preserve_invariants() -> None:
    def synchronous(request: Delivery, context: Context) -> DeliveryResult:
        return DeliveryResult(accepted=True)

    with pytest.raises(JobBindingError, match="async function"):
        JobHandler(
            job=delivery_job,
            use_case=synchronous,  # type: ignore[arg-type]
        )

    binding = job_handler(delivery_job, deliver)
    with pytest.raises(ConfigurationError, match="must be a tuple"):
        JobGroup(handlers=[binding])  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="duplicate job name"):
        JobGroup(handlers=(binding, binding))


def test_direct_job_dispatcher_construction_preserves_invariants() -> None:
    with pytest.raises(ConfigurationError, match="must be a JobGroup"):
        JobDispatcher(jobs=cast(JobGroup, object()))

    def invalid_observer() -> None:
        return None

    with pytest.raises(ConfigurationError, match="JobDispatcher"):
        JobDispatcher(
            jobs=job_group(),
            use_case_observers=(
                invalid_observer,  # pyright: ignore[reportArgumentType]
            ),
        )


@dataclass(frozen=True, slots=True)
class _ManifestProtocolChange:
    severity: ChangeSeverity
    location: str
    message: str


class _ManifestChangeSink:
    def __init__(self) -> None:
        self.changes: list[_ManifestProtocolChange] = []

    def add(self, severity: ChangeSeverity, location: str, message: str) -> None:
        self.changes.append(_ManifestProtocolChange(severity, location, message))


def _render_manifest_protocol() -> dict[str, object]:
    adapter = TypeAdapter(JobManifest)
    adapter.validate_python(
        {"schema_version": JOB_MANIFEST_VERSION, "jobs": []},
        strict=True,
    )
    return {
        "schema_version": JOB_MANIFEST_VERSION,
        "manifest_schema": adapter.json_schema(mode="serialization"),
    }


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(dict[str, object], mapping)


def _load_manifest_protocol() -> dict[str, object]:
    return _object(json.loads(JOB_MANIFEST_SNAPSHOT_PATH.read_text(encoding="utf-8")))


def _manifest_protocol_incompatibilities(
    baseline: dict[str, object],
    current: dict[str, object],
) -> list[_ManifestProtocolChange]:
    baseline_schema = _object(baseline["manifest_schema"])
    current_schema = _object(current["manifest_schema"])
    sink = _ManifestChangeSink()
    compare_schema(
        {key: value for key, value in baseline_schema.items() if key != "$defs"},
        {key: value for key, value in current_schema.items() if key != "$defs"},
        direction="output",
        baseline=baseline_schema,
        current=current_schema,
        location="job manifest",
        changes=sink,
    )
    return [
        change for change in sink.changes if change.severity in ("breaking", "unknown")
    ]


def _manifest_snapshot_version(path: Path) -> int | None:
    prefix = "job_manifest_protocol_v"
    suffix = "_snapshot.json"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        return None
    raw_version = path.name[len(prefix) : -len(suffix)]
    return int(raw_version) if raw_version.isdigit() else None


def _versioned_manifest_snapshot_paths() -> dict[int, Path]:
    snapshots: dict[int, Path] = {}
    for path in Path(__file__).parent.glob("job_manifest_protocol_v*_snapshot.json"):
        version = _manifest_snapshot_version(path)
        assert version is not None
        assert version not in snapshots
        snapshots[version] = path
    return snapshots


def _git_manifest_snapshot_texts(base_ref: str) -> dict[int, str]:
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_ref, "--", "tests"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0
    snapshots: dict[int, str] = {}
    for relative in listed.stdout.splitlines():
        path = Path(relative)
        version = _manifest_snapshot_version(path)
        if path.parent != Path("tests") or version is None:
            continue
        shown = subprocess.run(
            ["git", "show", f"{base_ref}:{relative}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert shown.returncode == 0
        snapshots[version] = shown.stdout
    return snapshots


def test_job_manifest_protocol_matches_its_versioned_snapshot() -> None:
    current = _render_manifest_protocol()
    if os.environ.get(UPDATE_JOB_MANIFEST_SNAPSHOT):
        if JOB_MANIFEST_SNAPSHOT_PATH.exists():
            assert not _manifest_protocol_incompatibilities(
                _load_manifest_protocol(), current
            ), "Breaking job-manifest changes require a new JOB_MANIFEST_VERSION"
        JOB_MANIFEST_SNAPSHOT_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    assert JOB_MANIFEST_SNAPSHOT_PATH.exists(), (
        f"Generate the job manifest snapshot with "
        f"{UPDATE_JOB_MANIFEST_SNAPSHOT}=1 uv run pytest tests/test_jobs.py"
    )
    baseline = _load_manifest_protocol()
    assert not _manifest_protocol_incompatibilities(baseline, current)
    assert current == baseline, (
        "The job manifest protocol changed without updating its versioned snapshot"
    )


def test_job_manifest_snapshot_history_is_complete() -> None:
    assert set(_versioned_manifest_snapshot_paths()) == set(
        range(1, JOB_MANIFEST_VERSION + 1)
    )


def test_job_manifest_protocol_matches_its_git_baseline() -> None:
    base_ref = os.environ.get(JOB_MANIFEST_BASE_REF)
    if base_ref is None:
        return
    baseline_snapshots = _git_manifest_snapshot_texts(base_ref)
    if not baseline_snapshots:
        assert JOB_MANIFEST_VERSION == 1
        return
    previous_version = max(baseline_snapshots)
    assert set(baseline_snapshots) == set(range(1, previous_version + 1))
    assert JOB_MANIFEST_VERSION in (previous_version, previous_version + 1)
    local_snapshots = _versioned_manifest_snapshot_paths()
    for version, baseline_text in baseline_snapshots.items():
        if version >= JOB_MANIFEST_VERSION:
            continue
        assert local_snapshots[version].read_text(encoding="utf-8") == baseline_text
    if previous_version == JOB_MANIFEST_VERSION:
        baseline = _object(json.loads(baseline_snapshots[JOB_MANIFEST_VERSION]))
        assert not _manifest_protocol_incompatibilities(
            baseline, _render_manifest_protocol()
        )


def test_same_job_manifest_version_rejects_breaking_changes() -> None:
    baseline = _render_manifest_protocol()
    current = _object(json.loads(json.dumps(baseline)))
    schema = _object(current["manifest_schema"])
    _object(schema["properties"]).pop("jobs")

    incompatible = _manifest_protocol_incompatibilities(baseline, current)

    assert any(change.severity == "breaking" for change in incompatible)
