"""Infrastructure-neutral fixed-window rate limiting."""

import asyncio
import math
from collections.abc import Callable

import pytest

from tenchi.errors import AppError, ConfigurationError
from tenchi.rate_limits import (
    RATE_LIMITED,
    MemoryRateLimitStore,
    RateLimitDecision,
    RateLimitExceeded,
    RateLimitPermit,
    RateLimitStoreError,
    enforce_rate_limit,
)


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
        decision: RateLimitDecision | Callable[..., RateLimitDecision],
    ) -> None:
        self._decision = decision
        self.calls: list[dict[str, object]] = []

    async def consume(
        self,
        *,
        namespace: str,
        scope: str,
        limit: int,
        window_seconds: float,
        cost: int,
    ) -> RateLimitDecision:
        call: dict[str, object] = {
            "namespace": namespace,
            "scope": scope,
            "limit": limit,
            "window_seconds": window_seconds,
            "cost": cost,
        }
        self.calls.append(call)
        if callable(self._decision):
            return self._decision(**call)
        return self._decision


async def test_enforce_returns_validated_permit_and_normalizes_window() -> None:
    store = Store(
        RateLimitPermit(
            limit=5,
            remaining=3,
            reset_after_seconds=30.0,
        )
    )

    permit = await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="actor:alice",
        limit=5,
        window_seconds=60,
        cost=2,
    )

    assert permit == RateLimitPermit(
        limit=5,
        remaining=3,
        reset_after_seconds=30.0,
    )
    assert store.calls == [
        {
            "namespace": "tasks.create",
            "scope": "actor:alice",
            "limit": 5,
            "window_seconds": 60.0,
            "cost": 2,
        }
    ]


async def test_exceeded_window_raises_standard_error_with_retry_after() -> None:
    store = Store(RateLimitExceeded(limit=5, retry_after_seconds=17))

    with pytest.raises(AppError) as excinfo:
        await enforce_rate_limit(
            store,
            namespace="tasks.create",
            scope="alice",
            limit=5,
            window_seconds=60,
        )

    assert excinfo.value.definition == RATE_LIMITED
    assert excinfo.value.headers == {"Retry-After": "17"}


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"namespace": "Tasks Create"}, "dotted snake_case"),
        ({"scope": ""}, "scope"),
        ({"limit": 0}, "limit"),
        ({"limit": True}, "limit"),
        ({"cost": 0}, "cost"),
        ({"cost": True}, "cost"),
        ({"limit": 2, "cost": 3}, "must not exceed"),
        ({"window_seconds": 0}, "window_seconds"),
        ({"window_seconds": math.inf}, "window_seconds"),
        ({"window_seconds": True}, "window_seconds"),
    ],
)
async def test_enforce_rejects_invalid_policy(
    overrides: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "namespace": "tasks.create",
        "scope": "alice",
        "limit": 5,
        "window_seconds": 60,
        "cost": 1,
    }
    values.update(overrides)
    store = Store(RateLimitPermit(5, 4, 60))

    with pytest.raises(ConfigurationError, match=message):
        await enforce_rate_limit(store, **values)  # type: ignore[arg-type]

    assert store.calls == []


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (object(), "expected a rate-limit decision"),
        (RateLimitPermit(4, 3, 10), "permit limit"),
        (RateLimitPermit(5.0, 4, 10), "permit limit"),  # type: ignore[arg-type]
        (RateLimitPermit(5, True, 10), "permit remaining"),
        (RateLimitPermit(5, 5, 10), "permit remaining"),
        (RateLimitPermit(5, 4, 0), "reset_after_seconds"),
        (RateLimitPermit(5, 4, 61), "reset_after_seconds"),
        (RateLimitExceeded(4, 10), "exceeded limit"),
        (RateLimitExceeded(5.0, 10), "exceeded limit"),  # type: ignore[arg-type]
        (RateLimitExceeded(5, True), "retry_after_seconds"),
        (RateLimitExceeded(5, 0), "retry_after_seconds"),
        (RateLimitExceeded(5, 61), "retry_after_seconds"),
    ],
)
async def test_enforce_fails_closed_on_invalid_store_decisions(
    decision: object,
    message: str,
) -> None:
    store = Store(decision)  # type: ignore[arg-type]

    with pytest.raises(RateLimitStoreError, match=message):
        await enforce_rate_limit(
            store,
            namespace="tasks.create",
            scope="alice",
            limit=5,
            window_seconds=60,
        )


async def test_memory_store_consumes_weighted_capacity_without_charging_denials() -> (
    None
):
    clock = Clock()
    store = MemoryRateLimitStore(clock=clock)

    first = await enforce_rate_limit(
        store,
        namespace="reports.export",
        scope="tenant:one",
        limit=5,
        window_seconds=60,
        cost=3,
    )
    with pytest.raises(AppError):
        await enforce_rate_limit(
            store,
            namespace="reports.export",
            scope="tenant:one",
            limit=5,
            window_seconds=60,
            cost=3,
        )
    final = await enforce_rate_limit(
        store,
        namespace="reports.export",
        scope="tenant:one",
        limit=5,
        window_seconds=60,
        cost=2,
    )

    assert first.remaining == 2
    assert final.remaining == 0


async def test_memory_store_resets_at_window_boundary_and_isolates_identities() -> None:
    clock = Clock()
    store = MemoryRateLimitStore(clock=clock)

    alice = await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="alice",
        limit=1,
        window_seconds=30,
    )
    bob = await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="bob",
        limit=1,
        window_seconds=30,
    )
    other_operation = await enforce_rate_limit(
        store,
        namespace="tasks.update",
        scope="alice",
        limit=1,
        window_seconds=30,
    )
    clock.advance(30)
    reset = await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="alice",
        limit=1,
        window_seconds=30,
    )

    assert alice.remaining == 0
    assert bob.remaining == 0
    assert other_operation.remaining == 0
    assert reset == RateLimitPermit(1, 0, 30.0)


async def test_memory_store_policy_change_starts_a_new_window() -> None:
    clock = Clock()
    store = MemoryRateLimitStore(clock=clock)
    await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="alice",
        limit=1,
        window_seconds=60,
    )

    changed = await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="alice",
        limit=2,
        window_seconds=30,
    )

    assert changed == RateLimitPermit(2, 1, 30.0)


async def test_memory_store_serializes_concurrent_consumers() -> None:
    store = MemoryRateLimitStore(clock=Clock())

    async def consume() -> bool:
        try:
            await enforce_rate_limit(
                store,
                namespace="tasks.create",
                scope="alice",
                limit=10,
                window_seconds=60,
            )
        except AppError as exc:
            assert exc.definition == RATE_LIMITED
            return False
        return True

    results = await asyncio.gather(*(consume() for _ in range(100)))

    assert results.count(True) == 10
    assert results.count(False) == 90


async def test_memory_store_rejects_invalid_direct_calls_and_clock_values() -> None:
    store = MemoryRateLimitStore(clock=lambda: math.nan)

    with pytest.raises(ConfigurationError, match="scope"):
        await store.consume(
            namespace="tasks.create",
            scope="",
            limit=1,
            window_seconds=60,
            cost=1,
        )
    with pytest.raises(RateLimitStoreError, match="clock"):
        await store.consume(
            namespace="tasks.create",
            scope="alice",
            limit=1,
            window_seconds=60,
            cost=1,
        )
    with pytest.raises(ConfigurationError, match="clock must be callable"):
        MemoryRateLimitStore(clock=object())  # type: ignore[arg-type]


async def test_memory_store_fails_closed_when_clock_moves_backwards() -> None:
    clock = Clock()
    store = MemoryRateLimitStore(clock=clock)
    await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="alice",
        limit=2,
        window_seconds=60,
    )
    clock.advance(-1)

    with pytest.raises(RateLimitStoreError, match="must not move backwards"):
        await enforce_rate_limit(
            store,
            namespace="tasks.create",
            scope="alice",
            limit=2,
            window_seconds=60,
        )

    clock.advance(1)
    permit = await enforce_rate_limit(
        store,
        namespace="tasks.create",
        scope="alice",
        limit=2,
        window_seconds=60,
    )
    assert permit.remaining == 0


async def test_memory_store_rejects_a_clock_without_future_precision() -> None:
    store = MemoryRateLimitStore(clock=lambda: 1e308)

    with pytest.raises(RateLimitStoreError, match="finite future reset"):
        await enforce_rate_limit(
            store,
            namespace="tasks.create",
            scope="alice",
            limit=1,
            window_seconds=60,
        )
