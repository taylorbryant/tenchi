from collections.abc import Callable
from math import ceil
from time import time
from uuid import uuid4

import aiosqlite
from tenchi.idempotency import (
    IdempotencyConflict,
    IdempotencyDecision,
    IdempotencyInProgress,
    IdempotencyReplay,
    IdempotencyReservation,
)

IDEMPOTENCY_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_records (
    namespace TEXT NOT NULL,
    scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'completed')),
    reservation_token TEXT NOT NULL,
    result_json BLOB,
    reservation_expires_at REAL NOT NULL,
    completed_expires_at REAL,
    PRIMARY KEY (namespace, scope, idempotency_key)
)
"""


class SqliteIdempotencyStore:
    """Durable transitions on the request-scoped transaction connection."""

    def __init__(
        self,
        connection: aiosqlite.Connection,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self._connection = connection
        self._clock = clock

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
        await self._connection.execute("DELETE FROM idempotency_records WHERE 0")
        now = self._clock()
        token = uuid4().hex
        cursor = await self._connection.execute(
            "INSERT INTO idempotency_records "
            "(namespace, scope, idempotency_key, fingerprint, state, "
            "reservation_token, result_json, reservation_expires_at, "
            "completed_expires_at) "
            "VALUES (?, ?, ?, ?, 'reserved', ?, NULL, ?, NULL) "
            "ON CONFLICT (namespace, scope, idempotency_key) DO UPDATE SET "
            "fingerprint = excluded.fingerprint, state = 'reserved', "
            "reservation_token = excluded.reservation_token, result_json = NULL, "
            "reservation_expires_at = excluded.reservation_expires_at, "
            "completed_expires_at = NULL "
            "WHERE (idempotency_records.state = 'reserved' "
            "AND idempotency_records.reservation_expires_at <= ?) "
            "OR (idempotency_records.state = 'completed' "
            "AND idempotency_records.completed_expires_at IS NOT NULL "
            "AND idempotency_records.completed_expires_at <= ?) "
            "RETURNING reservation_token",
            (
                namespace,
                scope,
                key,
                fingerprint,
                token,
                now + reservation_ttl,
                now,
                now,
            ),
        )
        if await cursor.fetchone() is not None:
            return IdempotencyReservation(
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint=fingerprint,
                token=token,
                completed_ttl=completed_ttl,
            )

        cursor = await self._connection.execute(
            "SELECT fingerprint, state, result_json, reservation_expires_at "
            "FROM idempotency_records "
            "WHERE namespace = ? AND scope = ? AND idempotency_key = ?",
            (namespace, scope, key),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("idempotency record disappeared during reservation")
        if str(row[0]) != fingerprint:
            return IdempotencyConflict()
        if str(row[1]) == "completed":
            if row[2] is None:
                raise RuntimeError("completed idempotency record has no result")
            result_json = (
                bytes(row[2]) if not isinstance(row[2], str) else row[2].encode()
            )
            return IdempotencyReplay(result_json=result_json)
        if str(row[1]) == "reserved":
            return IdempotencyInProgress(
                retry_after_seconds=max(1, ceil(float(row[3]) - now))
            )
        raise RuntimeError(f"unknown idempotency state {row[1]!r}")

    async def complete(
        self,
        reservation: IdempotencyReservation,
        *,
        result_json: bytes,
    ) -> None:
        await self._connection.execute("DELETE FROM idempotency_records WHERE 0")
        now = self._clock()
        completed_expires_at = (
            None
            if reservation.completed_ttl is None
            else now + reservation.completed_ttl
        )
        cursor = await self._connection.execute(
            "UPDATE idempotency_records "
            "SET state = 'completed', result_json = ?, completed_expires_at = ? "
            "WHERE namespace = ? AND scope = ? AND idempotency_key = ? "
            "AND fingerprint = ? AND reservation_token = ? AND state = 'reserved' "
            "AND reservation_expires_at > ? RETURNING namespace",
            (
                result_json,
                completed_expires_at,
                reservation.namespace,
                reservation.scope,
                reservation.key,
                reservation.fingerprint,
                reservation.token,
                now,
            ),
        )
        if await cursor.fetchone() is None:
            raise RuntimeError("idempotency reservation is no longer active")

    async def abandon(self, reservation: IdempotencyReservation) -> None:
        await self._connection.execute(
            "DELETE FROM idempotency_records "
            "WHERE namespace = ? AND scope = ? AND idempotency_key = ? "
            "AND fingerprint = ? AND reservation_token = ? AND state = 'reserved'",
            (
                reservation.namespace,
                reservation.scope,
                reservation.key,
                reservation.fingerprint,
                reservation.token,
            ),
        )
