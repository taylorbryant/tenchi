"""Concrete runtime wiring for Fieldnotes ports."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from app.features.knowledge.ports import (
    AnswerGenerator,
    Outbox,
    PassageIndex,
    SourceRepository,
)

from .deterministic_answer_generator import DeterministicAnswerGenerator
from .sqlite_knowledge import (
    SqliteOutbox,
    SqlitePassageIndex,
    SqliteSourceRepository,
    configure_connection,
)
from .sqlite_knowledge import (
    ensure_schema as ensure_sqlite_schema,
)


@dataclass(frozen=True, slots=True)
class RequestPorts:
    sources: SourceRepository
    passages: PassageIndex
    answer_generator: AnswerGenerator
    outbox: Outbox


async def ensure_schema(database_path: str) -> None:
    await ensure_sqlite_schema(database_path)


@asynccontextmanager
async def open_request_ports(database_path: str) -> AsyncGenerator[RequestPorts]:
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        sources = SqliteSourceRepository(connection)
        passages = SqlitePassageIndex(connection)
        try:
            yield RequestPorts(
                sources=sources,
                passages=passages,
                answer_generator=DeterministicAnswerGenerator(),
                outbox=SqliteOutbox(connection),
            )
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise


@asynccontextmanager
async def open_preflight_connection(
    database_path: str,
) -> AsyncGenerator[aiosqlite.Connection]:
    uri = f"{Path(database_path).resolve().as_uri()}?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as connection:
        await configure_connection(connection)
        await connection.execute("PRAGMA query_only = ON")
        yield connection
