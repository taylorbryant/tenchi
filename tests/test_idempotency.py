import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_serializer

from tenchi.errors import AppError, ConfigurationError
from tenchi.idempotency import (
    IDEMPOTENCY_CONFLICT,
    IDEMPOTENCY_IN_PROGRESS,
    IdempotencyConflict,
    IdempotencyDecision,
    IdempotencyInProgress,
    IdempotencyReplay,
    IdempotencyReservation,
    IdempotencyResultError,
    IdempotencyStoreError,
    MemoryIdempotencyStore,
    fingerprint,
    run_idempotently,
)


class Request(BaseModel):
    title: str
    priority: int = Field(alias="Priority")


class Result(BaseModel):
    id: str
    title: str


class SetRequest(BaseModel):
    tags: set[str]


class AnyRequest(BaseModel):
    value: Any


class DatedRequest(BaseModel):
    occurred_at: datetime


class OtherRequest(BaseModel):
    title: str
    priority: int = Field(alias="Priority")


class StringKind(StrEnum):
    primary = "primary"


def serialize_as_one(value: int) -> str:
    del value
    return "1"


def collapse_values(values: list[int]) -> list[int]:
    values[:] = [1]
    return values


class LossySerializedRequest(BaseModel):
    value: Annotated[int, PlainSerializer(serialize_as_one, return_type=str)]


class MutatingSerializedRequest(BaseModel):
    values: Annotated[
        list[int],
        PlainSerializer(collapse_values, return_type=list[int]),
    ]


class ExtraRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


unstable_serializations = 0


class UnstableSerializedRequest(BaseModel):
    value: int

    @model_serializer
    def serialize_model(self) -> dict[str, int]:
        global unstable_serializations
        unstable_serializations += 1
        return {"value": self.value, "nonce": unstable_serializations}


class Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Store:
    def __init__(
        self,
        decision: IdempotencyDecision | Callable[..., IdempotencyDecision],
    ) -> None:
        self._decision = decision
        self.reservations: list[dict[str, object]] = []
        self.completed: list[tuple[IdempotencyReservation, bytes]] = []
        self.abandoned: list[IdempotencyReservation] = []
        self.complete_error: BaseException | None = None
        self.abandon_error: BaseException | None = None

    async def reserve(
        self,
        *,
        namespace: str,
        scope: str,
        key: str,
        fingerprint: str,
        completed_ttl: float | None,
        reservation_ttl: float,
    ) -> IdempotencyDecision:
        values: dict[str, object] = {
            "namespace": namespace,
            "scope": scope,
            "key": key,
            "fingerprint": fingerprint,
            "completed_ttl": completed_ttl,
            "reservation_ttl": reservation_ttl,
        }
        self.reservations.append(values)
        if callable(self._decision):
            return self._decision(**values)
        return self._decision

    async def complete(
        self,
        reservation: IdempotencyReservation,
        *,
        result_json: bytes,
    ) -> None:
        if self.complete_error is not None:
            raise self.complete_error
        self.completed.append((reservation, result_json))

    async def abandon(self, reservation: IdempotencyReservation) -> None:
        self.abandoned.append(reservation)
        if self.abandon_error is not None:
            raise self.abandon_error


def reserved(
    *,
    namespace: str,
    scope: str,
    key: str,
    fingerprint: str,
    completed_ttl: float | None,
    reservation_ttl: float,
) -> IdempotencyReservation:
    del reservation_ttl
    return IdempotencyReservation(
        namespace=namespace,
        scope=scope,
        key=key,
        fingerprint=fingerprint,
        token="reservation-1",
        completed_ttl=completed_ttl,
    )


def test_fingerprint_canonicalizes_validated_json() -> None:
    left = fingerprint(
        {"title": "Ship", "Priority": "2"},
        annotation=Request,
    )
    right = fingerprint(
        {"Priority": 2, "title": "Ship"},
        annotation=Request,
    )

    assert left == right
    assert len(left) == 64


def test_fingerprint_distinguishes_types_and_rejects_non_json_numbers() -> None:
    assert fingerprint(1) != fingerprint("1")
    assert fingerprint(b"abc") != fingerprint("abc")
    occurred_at = datetime(2026, 8, 8, tzinfo=UTC)
    assert fingerprint(occurred_at) != fingerprint(occurred_at.isoformat())

    with pytest.raises(ConfigurationError, match="canonical JSON"):
        fingerprint(math.nan)
    with pytest.raises(ConfigurationError, match="canonical JSON"):
        fingerprint({"unsupported": object()})


def test_annotation_less_fingerprint_namespaces_non_json_root_types() -> None:
    request = Request.model_validate({"title": "Ship", "Priority": 2})
    other = OtherRequest.model_validate({"title": "Ship", "Priority": 2})

    assert fingerprint(request) != fingerprint(other)
    assert fingerprint(StringKind.primary) != fingerprint("primary")


def test_fingerprint_compares_validated_values_before_custom_serialization() -> None:
    assert (
        len(
            fingerprint(
                LossySerializedRequest(value=1),
                annotation=LossySerializedRequest,
            )
        )
        == 64
    )

    with pytest.raises(ConfigurationError, match="serialization must preserve"):
        fingerprint(
            LossySerializedRequest(value=2),
            annotation=LossySerializedRequest,
        )

    with pytest.raises(ConfigurationError, match="serialization must preserve"):
        fingerprint(
            ExtraRequest.model_validate({"value": b"abc"}),
            annotation=ExtraRequest,
        )

    mutating = MutatingSerializedRequest(values=[1, 2])
    with pytest.raises(ConfigurationError, match="serialization must preserve"):
        fingerprint(mutating, annotation=MutatingSerializedRequest)
    assert mutating.values == [1, 2]


def test_fingerprint_rejects_unstable_serialized_fields() -> None:
    unstable = UnstableSerializedRequest(value=1)

    with pytest.raises(ConfigurationError, match="stable after validation"):
        fingerprint(unstable, annotation=UnstableSerializedRequest)

    assert unstable.value == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, b"abc"])
def test_fingerprint_rejects_lossy_untyped_nested_values(value: object) -> None:
    with pytest.raises(ConfigurationError, match="serialization must preserve"):
        fingerprint({"value": value})


@pytest.mark.parametrize("value", [math.nan, math.inf, b"abc"])
def test_fingerprint_rejects_lossy_any_values(value: object) -> None:
    with pytest.raises(ConfigurationError, match="serialization must preserve"):
        fingerprint(AnyRequest(value=value), annotation=AnyRequest)


def test_fingerprint_preserves_typed_json_coercion() -> None:
    occurred_at = datetime(2026, 8, 8, tzinfo=UTC)

    assert fingerprint(
        {"occurred_at": occurred_at.isoformat()}, annotation=DatedRequest
    ) == fingerprint(DatedRequest(occurred_at=occurred_at), annotation=DatedRequest)


def test_fingerprint_rejects_unordered_collections() -> None:
    with pytest.raises(ConfigurationError, match="sorted tuple or list"):
        fingerprint({"tags": ["one", "two"]}, annotation=SetRequest)


async def test_reserved_operation_is_validated_serialized_and_completed() -> None:
    store = Store(reserved)
    calls = 0

    async def operation() -> Result:
        nonlocal calls
        calls += 1
        return Result(id="task-1", title="Ship")

    result = await run_idempotently(
        store,
        namespace="tasks.create",
        scope="actor:alice",
        key="request-1",
        fingerprint="input-1",
        result_type=Result,
        operation=operation,
        completed_ttl=3600,
        reservation_ttl=10,
    )

    assert result == Result(id="task-1", title="Ship")
    assert calls == 1
    assert store.reservations == [
        {
            "namespace": "tasks.create",
            "scope": "actor:alice",
            "key": "request-1",
            "fingerprint": "input-1",
            "completed_ttl": 3600.0,
            "reservation_ttl": 10.0,
        }
    ]
    assert len(store.completed) == 1
    assert Result.model_validate_json(store.completed[0][1]) == result
    assert store.abandoned == []


async def test_replay_is_validated_without_calling_operation() -> None:
    store = Store(
        IdempotencyReplay(
            result_json=Result(id="task-1", title="Original").model_dump_json().encode()
        )
    )

    async def operation() -> Result:
        raise AssertionError("replay must not invoke the operation")

    result = await run_idempotently(
        store,
        namespace="tasks.create",
        scope="actor:alice",
        key="request-1",
        fingerprint="input-1",
        result_type=Result,
        operation=operation,
    )

    assert result == Result(id="task-1", title="Original")
    assert store.completed == []
    assert store.abandoned == []


@pytest.mark.parametrize(
    ("decision", "definition", "headers"),
    [
        (IdempotencyConflict(), IDEMPOTENCY_CONFLICT, {}),
        (IdempotencyInProgress(), IDEMPOTENCY_IN_PROGRESS, {}),
        (
            IdempotencyInProgress(retry_after_seconds=7),
            IDEMPOTENCY_IN_PROGRESS,
            {"Retry-After": "7"},
        ),
    ],
)
async def test_non_executable_decisions_raise_standard_declared_errors(
    decision: IdempotencyDecision,
    definition: object,
    headers: dict[str, str],
) -> None:
    store = Store(decision)

    async def operation() -> Result:
        raise AssertionError("decision must not invoke the operation")

    with pytest.raises(AppError) as excinfo:
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=Result,
            operation=operation,
        )

    assert excinfo.value.definition == definition
    assert excinfo.value.headers == headers


@pytest.mark.parametrize(
    "failure",
    [RuntimeError("operation failed"), asyncio.CancelledError()],
)
async def test_operation_failure_or_cancellation_abandons_before_propagating(
    failure: BaseException,
) -> None:
    store = Store(reserved)

    async def operation() -> Result:
        raise failure

    with pytest.raises(type(failure), match=str(failure) or None):
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=Result,
            operation=operation,
        )

    assert len(store.abandoned) == 1
    assert store.completed == []


async def test_invalid_operation_result_abandons_the_reservation() -> None:
    store = Store(reserved)

    async def operation() -> Result:
        return {"id": "missing-title"}  # pyright: ignore[reportReturnType]

    with pytest.raises(IdempotencyResultError, match="operation result"):
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=Result,
            operation=operation,
        )

    assert len(store.abandoned) == 1


async def test_invalid_replay_fails_closed_without_abandoning_another_owner() -> None:
    store = Store(IdempotencyReplay(result_json=b'{"id":"missing-title"}'))

    async def operation() -> Result:
        raise AssertionError

    with pytest.raises(IdempotencyResultError, match="stored replay"):
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=Result,
            operation=operation,
        )

    assert store.abandoned == []


@pytest.mark.parametrize(
    "decision",
    [
        IdempotencyReplay(result_json="{}"),  # pyright: ignore[reportArgumentType]
        IdempotencyInProgress(retry_after_seconds=0),
        IdempotencyReservation(
            namespace="other.operation",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            token="reservation-1",
            completed_ttl=None,
        ),
        IdempotencyReservation(
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            token="",
            completed_ttl=None,
        ),
    ],
)
async def test_malformed_store_decisions_fail_before_the_operation(
    decision: IdempotencyDecision,
) -> None:
    store = Store(decision)

    async def operation() -> Result:
        raise AssertionError("malformed decisions must not run the operation")

    with pytest.raises(IdempotencyStoreError):
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=Result,
            operation=operation,
        )

    assert store.completed == []
    assert store.abandoned == []


async def test_completion_failure_attempts_abandon_without_masking_failure() -> None:
    store = Store(reserved)
    store.complete_error = RuntimeError("database unavailable")
    store.abandon_error = RuntimeError("cleanup unavailable")

    async def operation() -> Result:
        return Result(id="task-1", title="Ship")

    with pytest.raises(RuntimeError, match="database unavailable"):
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=Result,
            operation=operation,
        )

    assert len(store.abandoned) == 1


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"namespace": "Tasks.Create"}, "dotted snake_case"),
        ({"scope": ""}, "scope must be"),
        ({"key": ""}, "key must be"),
        ({"fingerprint": ""}, "fingerprint must be"),
        ({"completed_ttl": 0}, "completed_ttl"),
        ({"reservation_ttl": float("inf")}, "reservation_ttl"),
    ],
)
async def test_invalid_configuration_fails_before_storage(
    overrides: dict[str, object],
    message: str,
) -> None:
    store = Store(reserved)

    async def operation() -> Result:
        return Result(id="task-1", title="Ship")

    arguments: dict[str, object] = {
        "namespace": "tasks.create",
        "scope": "actor:alice",
        "key": "request-1",
        "fingerprint": "input-1",
        "result_type": Result,
        "operation": operation,
    }
    arguments.update(overrides)

    with pytest.raises(ConfigurationError, match=message):
        await run_idempotently(store, **arguments)  # pyright: ignore[reportArgumentType]

    assert store.reservations == []


async def test_invalid_result_type_fails_before_storage() -> None:
    store = Store(reserved)

    async def operation() -> Awaitable[object]:
        raise AssertionError

    with pytest.raises(ConfigurationError, match="result_type"):
        await run_idempotently(
            store,
            namespace="tasks.create",
            scope="actor:alice",
            key="request-1",
            fingerprint="input-1",
            result_type=asyncio.Lock,
            operation=operation,
        )

    assert store.reservations == []


async def test_memory_store_rejects_invalid_direct_calls_and_clock_values() -> None:
    store = MemoryIdempotencyStore(clock=lambda: math.nan)

    with pytest.raises(ConfigurationError, match="scope"):
        await store.reserve(
            namespace="tasks.create",
            scope="",
            key="request-1",
            fingerprint="input-1",
            completed_ttl=None,
            reservation_ttl=60,
        )
    with pytest.raises(IdempotencyStoreError, match="clock"):
        await store.reserve(
            namespace="tasks.create",
            scope="alice",
            key="request-1",
            fingerprint="input-1",
            completed_ttl=None,
            reservation_ttl=60,
        )
    with pytest.raises(ConfigurationError, match="clock must be callable"):
        MemoryIdempotencyStore(clock=object())  # type: ignore[arg-type]


async def test_memory_store_fails_closed_when_clock_moves_backwards() -> None:
    clock = Clock()
    store = MemoryIdempotencyStore(clock=clock)
    await store.reserve(
        namespace="tasks.create",
        scope="alice",
        key="request-1",
        fingerprint="input-1",
        completed_ttl=None,
        reservation_ttl=60,
    )
    clock.advance(-1)

    with pytest.raises(IdempotencyStoreError, match="must not move backwards"):
        await store.reserve(
            namespace="tasks.create",
            scope="alice",
            key="request-1",
            fingerprint="input-1",
            completed_ttl=None,
            reservation_ttl=60,
        )

    clock.advance(1)
    decision = await store.reserve(
        namespace="tasks.create",
        scope="alice",
        key="request-1",
        fingerprint="input-1",
        completed_ttl=None,
        reservation_ttl=60,
    )
    assert decision == IdempotencyInProgress(retry_after_seconds=60)


async def test_memory_store_rejects_a_clock_without_future_precision() -> None:
    store = MemoryIdempotencyStore(clock=lambda: 1e308)

    with pytest.raises(IdempotencyStoreError, match="finite future expiration"):
        await store.reserve(
            namespace="tasks.create",
            scope="alice",
            key="request-1",
            fingerprint="input-1",
            completed_ttl=None,
            reservation_ttl=60,
        )
