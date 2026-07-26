"""In-memory idempotency storage for use-case and application tests."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from time import monotonic
from uuid import uuid4

from tenchi.idempotency import (
    IdempotencyConflict,
    IdempotencyDecision,
    IdempotencyInProgress,
    IdempotencyReplay,
    IdempotencyReservation,
)


@dataclass(slots=True)
class _Record:
    fingerprint: str
    token: str
    result_json: bytes | None
    reservation_expires_at: float
    completed_expires_at: float | None = None


class MemoryIdempotencyStore:
    """Process-local adapter for tests, never a production durability boundary."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._records: dict[tuple[str, str, str], _Record] = {}

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
        identity = (namespace, scope, key)
        now = self._clock()
        async with self._lock:
            existing = self._records.get(identity)
            if existing is not None and _expired(existing, now=now):
                del self._records[identity]
                existing = None
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    return IdempotencyConflict()
                if existing.result_json is not None:
                    return IdempotencyReplay(result_json=bytes(existing.result_json))
                retry_after = max(1, ceil(existing.reservation_expires_at - now))
                return IdempotencyInProgress(retry_after_seconds=retry_after)

            token = uuid4().hex
            self._records[identity] = _Record(
                fingerprint=fingerprint,
                token=token,
                result_json=None,
                reservation_expires_at=now + reservation_ttl,
            )
            return IdempotencyReservation(
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint=fingerprint,
                token=token,
                completed_ttl=completed_ttl,
            )

    async def complete(
        self,
        reservation: IdempotencyReservation,
        *,
        result_json: bytes,
    ) -> None:
        identity = (reservation.namespace, reservation.scope, reservation.key)
        async with self._lock:
            existing = self._records.get(identity)
            if (
                existing is None
                or existing.token != reservation.token
                or existing.fingerprint != reservation.fingerprint
                or existing.result_json is not None
            ):
                raise RuntimeError("idempotency reservation is no longer active")
            existing.result_json = bytes(result_json)
            existing.completed_expires_at = (
                None
                if reservation.completed_ttl is None
                else self._clock() + reservation.completed_ttl
            )

    async def abandon(self, reservation: IdempotencyReservation) -> None:
        identity = (reservation.namespace, reservation.scope, reservation.key)
        async with self._lock:
            existing = self._records.get(identity)
            if (
                existing is not None
                and existing.token == reservation.token
                and existing.result_json is None
            ):
                del self._records[identity]


def _expired(record: _Record, *, now: float) -> bool:
    if record.result_json is None:
        return record.reservation_expires_at <= now
    return (
        record.completed_expires_at is not None and record.completed_expires_at <= now
    )
