"""The built-in memory adapters satisfy Tenchi's reusable store contracts."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest

from tenchi.idempotency import (
    IdempotencyConflict,
    IdempotencyDecision,
    IdempotencyReservation,
    IdempotencyStore,
    MemoryIdempotencyStore,
)
from tenchi.rate_limits import (
    MemoryRateLimitStore,
    RateLimitDecision,
    RateLimitPermit,
    RateLimitStore,
)
from tenchi.testing import (
    StoreConformanceError,
    verify_idempotency_store,
    verify_rate_limit_store,
)


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_memory_idempotency_store_conforms() -> None:
    clock = Clock()
    store = MemoryIdempotencyStore(clock=clock)

    @asynccontextmanager
    async def open_store() -> AsyncGenerator[IdempotencyStore]:
        yield store

    await verify_idempotency_store(open_store, advance=clock.advance)


async def test_memory_rate_limit_store_conforms() -> None:
    clock = Clock()
    store = MemoryRateLimitStore(clock=clock)

    @asynccontextmanager
    async def open_store() -> AsyncGenerator[RateLimitStore]:
        yield store

    async def advance(seconds: float) -> None:
        clock.advance(seconds)

    await verify_rate_limit_store(open_store, advance=advance)


class ConflictOnlyIdempotencyStore:
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
        return IdempotencyConflict()

    async def complete(
        self,
        reservation: IdempotencyReservation,
        *,
        result_json: bytes,
    ) -> None:
        return None

    async def abandon(self, reservation: IdempotencyReservation) -> None:
        return None


async def test_idempotency_failure_names_the_store_and_case() -> None:
    store = ConflictOnlyIdempotencyStore()

    @asynccontextmanager
    async def open_store() -> AsyncGenerator[IdempotencyStore]:
        yield store

    with pytest.raises(StoreConformanceError) as excinfo:
        await verify_idempotency_store(open_store, advance=lambda _: None)

    assert excinfo.value.store == "IdempotencyStore"
    assert excinfo.value.case == "reservation_and_replay"
    assert "first reserve" in excinfo.value.reason


class NonConsumingRateLimitStore:
    async def consume(
        self,
        *,
        namespace: str,
        scope: str,
        limit: int,
        window_seconds: float,
        cost: int,
    ) -> RateLimitDecision:
        return RateLimitPermit(
            limit=limit,
            remaining=limit,
            reset_after_seconds=window_seconds,
        )


async def test_rate_limit_failure_names_the_store_and_case() -> None:
    store = NonConsumingRateLimitStore()

    @asynccontextmanager
    async def open_store() -> AsyncGenerator[RateLimitStore]:
        yield store

    with pytest.raises(StoreConformanceError) as excinfo:
        await verify_rate_limit_store(open_store, advance=lambda _: None)

    assert excinfo.value.store == "RateLimitStore"
    assert excinfo.value.case == "capacity_and_rejection"
    assert "first cost" in excinfo.value.reason
