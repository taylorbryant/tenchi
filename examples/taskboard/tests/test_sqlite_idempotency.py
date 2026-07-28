"""The taskboard's transactional SQLite idempotency adapter."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest

from app.infra.port_wiring import configure_connection, ensure_schema
from app.infra.sqlite_idempotency import SqliteIdempotencyStore
from tenchi.idempotency import IdempotencyReservation, IdempotencyStore
from tenchi.testing import verify_idempotency_store


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_sqlite_idempotency_store_conforms(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    clock = Clock()

    @asynccontextmanager
    async def open_store() -> AsyncGenerator[IdempotencyStore]:
        async with aiosqlite.connect(database) as connection:
            await configure_connection(connection)
            try:
                yield SqliteIdempotencyStore(connection, clock=clock)
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    await verify_idempotency_store(open_store, advance=clock.advance)


async def test_sqlite_samples_lease_time_after_acquiring_the_writer_lock(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    clock = Clock()

    async with (
        aiosqlite.connect(database) as blocker,
        aiosqlite.connect(database) as contender,
    ):
        await configure_connection(blocker)
        await configure_connection(contender)
        await blocker.execute("DELETE FROM idempotency_records WHERE 0")

        store = SqliteIdempotencyStore(contender, clock=clock)
        calls_before_reserve = clock.calls
        reserve = asyncio.create_task(
            store.reserve(
                namespace="tasks.create",
                scope="alice",
                key="request-1",
                fingerprint="input-1",
                completed_ttl=None,
                reservation_ttl=10,
            )
        )
        await asyncio.sleep(0)
        assert clock.calls == calls_before_reserve

        clock.advance(100)
        await blocker.commit()
        reservation = await reserve
        assert isinstance(reservation, IdempotencyReservation)
        await contender.commit()

        await blocker.execute("DELETE FROM idempotency_records WHERE 0")
        calls_before_complete = clock.calls
        complete = asyncio.create_task(
            store.complete(reservation, result_json=b'{"ok":true}')
        )
        await asyncio.sleep(0)
        assert clock.calls == calls_before_complete

        clock.advance(10)
        await blocker.commit()
        with pytest.raises(RuntimeError, match="no longer active"):
            await complete
