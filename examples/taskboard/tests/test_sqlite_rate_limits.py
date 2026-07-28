"""The taskboard's process-shared SQLite rate-limit adapter."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest

from app.infra.port_wiring import (
    configure_connection,
    ensure_schema,
    open_request_ports,
)
from app.infra.sqlite_rate_limits import SqliteRateLimitStore
from app.shared.users import OwnerScope
from tenchi.errors import AppError
from tenchi.rate_limits import (
    RATE_LIMITED,
    RateLimitPermit,
    RateLimitStore,
    enforce_rate_limit,
)
from tenchi.testing import verify_rate_limit_store


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


async def test_sqlite_rate_limit_store_conforms(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    clock = Clock()

    @asynccontextmanager
    async def open_store() -> AsyncGenerator[RateLimitStore]:
        async with aiosqlite.connect(database) as connection:
            await configure_connection(connection)
            try:
                yield SqliteRateLimitStore(connection, clock=clock)
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    await verify_rate_limit_store(open_store, advance=clock.advance)


async def test_sqlite_rate_limits_persist_and_reset_across_store_instances(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    clock = Clock()

    async with aiosqlite.connect(database) as connection:
        await configure_connection(connection)
        first = await enforce_rate_limit(
            SqliteRateLimitStore(connection, clock=clock),
            namespace="tasks.create",
            scope="alice",
            limit=1,
            window_seconds=60,
        )
        await connection.commit()
    async with aiosqlite.connect(database) as connection:
        await configure_connection(connection)
        with pytest.raises(AppError) as excinfo:
            await enforce_rate_limit(
                SqliteRateLimitStore(connection, clock=clock),
                namespace="tasks.create",
                scope="alice",
                limit=1,
                window_seconds=60,
            )
        await connection.commit()
    clock.value += 60
    async with aiosqlite.connect(database) as connection:
        await configure_connection(connection)
        reset = await enforce_rate_limit(
            SqliteRateLimitStore(connection, clock=clock),
            namespace="tasks.create",
            scope="alice",
            limit=1,
            window_seconds=60,
        )
        await connection.commit()

    assert first == RateLimitPermit(1, 0, 60.0)
    assert excinfo.value.definition == RATE_LIMITED
    assert reset == RateLimitPermit(1, 0, 60.0)


async def test_sqlite_rate_limits_have_exactly_one_set_of_concurrent_winners(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async def consume() -> bool:
        async with open_request_ports(database) as ports:
            try:
                await enforce_rate_limit(
                    ports.rate_limits,
                    namespace="tasks.create",
                    scope="alice",
                    limit=5,
                    window_seconds=60,
                )
            except AppError as exc:
                assert exc.definition == RATE_LIMITED
                return False
        return True

    results = await asyncio.gather(*(consume() for _ in range(20)))

    assert results.count(True) == 5
    assert results.count(False) == 15


async def test_rate_limit_consumption_rolls_back_with_the_logical_operation(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback):
        async with open_request_ports(database) as ports:
            await enforce_rate_limit(
                ports.rate_limits,
                namespace="projects.create",
                scope="alice",
                limit=1,
                window_seconds=60,
            )
            await ports.projects.create(
                name="Rolled back",
                owner=OwnerScope(owner_id="alice"),
            )
            raise Rollback

    async with open_request_ports(database) as ports:
        permit = await enforce_rate_limit(
            ports.rate_limits,
            namespace="projects.create",
            scope="alice",
            limit=1,
            window_seconds=60,
        )
        assert await ports.projects.list_owned_by(OwnerScope(owner_id="alice")) == []

    assert permit.remaining == 0
