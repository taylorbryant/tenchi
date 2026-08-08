"""Transactional SQLite adapters for sources, passages, and the outbox."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import aiosqlite
from pydantic import HttpUrl

from app.features.knowledge.policy import rank_texts
from app.features.knowledge.schemas import (
    Passage,
    PassageMatch,
    Source,
    SourceStatus,
    StoredSource,
)
from app.shared.users import OwnerScope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS sources_owner_idx ON sources(owner_id);

CREATE TABLE IF NOT EXISTS passages (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    position INTEGER NOT NULL,
    url TEXT
);

CREATE INDEX IF NOT EXISTS passages_owner_idx ON passages(owner_id, source_id);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    payload_json BLOB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    error TEXT
);
"""


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: int
    job: str
    payload_json: bytes


class SqliteSourceRepository:
    """Transactional source persistence."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create(
        self,
        *,
        title: str,
        content: str,
        url: str | None,
        owner: OwnerScope,
    ) -> Source:
        source = Source(
            id=uuid4().hex,
            title=title,
            url=HttpUrl(url) if url is not None else None,
            status=SourceStatus.PENDING,
        )
        await self._connection.execute(
            "INSERT INTO sources (id, owner_id, title, content, url, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                source.id,
                owner.owner_id,
                source.title,
                content,
                str(source.url) if source.url is not None else None,
                source.status.value,
            ),
        )
        return source

    async def list_owned_by(self, owner: OwnerScope) -> list[Source]:
        cursor = await self._connection.execute(
            "SELECT id, title, url, status FROM sources "
            "WHERE owner_id = ? ORDER BY rowid",
            (owner.owner_id,),
        )
        return [_row_to_source(row) for row in await cursor.fetchall()]

    async def get_for_indexing(self, source_id: str) -> StoredSource | None:
        cursor = await self._connection.execute(
            "SELECT id, owner_id, title, content, url, status "
            "FROM sources WHERE id = ?",
            (source_id,),
        )
        row = await cursor.fetchone()
        return _row_to_stored_source(row) if row is not None else None

    async def mark_indexed(self, source_id: str) -> None:
        await self._connection.execute(
            "UPDATE sources SET status = ? WHERE id = ?",
            (SourceStatus.INDEXED.value, source_id),
        )

    async def list_all(self) -> list[Source]:
        cursor = await self._connection.execute(
            "SELECT id, title, url, status FROM sources ORDER BY rowid"
        )
        return [_row_to_source(row) for row in await cursor.fetchall()]


class SqlitePassageIndex:
    """Owner-scoped deterministic passage retrieval."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def replace_source(
        self,
        source: StoredSource,
        passages: Sequence[Passage],
    ) -> None:
        await self._connection.execute(
            "DELETE FROM passages WHERE source_id = ?",
            (source.id,),
        )
        await self._connection.executemany(
            "INSERT INTO passages "
            "(id, source_id, owner_id, title, text, position, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    passage.id,
                    passage.source_id,
                    source.owner_id,
                    passage.title,
                    passage.text,
                    passage.position,
                    str(passage.url) if passage.url is not None else None,
                )
                for passage in passages
            ),
        )

    async def search(
        self,
        query: str,
        *,
        owner: OwnerScope,
        limit: int,
    ) -> list[PassageMatch]:
        cursor = await self._connection.execute(
            "SELECT id, source_id, title, text, position, url "
            "FROM passages WHERE owner_id = ?",
            (owner.owner_id,),
        )
        rows = await cursor.fetchall()
        by_id = {str(row[0]): row for row in rows}
        ranking = rank_texts(
            query,
            ((passage_id, f"{row[2]} {row[3]}") for passage_id, row in by_id.items()),
        )
        return [
            _row_to_match(by_id[item.id], score=item.score) for item in ranking[:limit]
        ]


class SqliteOutbox:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def enqueue(self, *, job: str, payload_json: bytes) -> None:
        await self._connection.execute(
            "INSERT INTO outbox (job, payload_json) VALUES (?, ?)",
            (job, payload_json),
        )

    async def claim_next(self) -> OutboxEntry | None:
        cursor = await self._connection.execute(
            "UPDATE outbox SET state = 'processing' "
            "WHERE id = ("
            "SELECT id FROM outbox WHERE state = 'pending' ORDER BY id LIMIT 1"
            ") AND state = 'pending' "
            "RETURNING id, job, payload_json"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return OutboxEntry(id=int(row[0]), job=str(row[1]), payload_json=bytes(row[2]))

    async def mark_delivered(self, entry_id: int) -> None:
        await self._connection.execute(
            "UPDATE outbox SET state = 'delivered' WHERE id = ?",
            (entry_id,),
        )

    async def mark_failed(self, entry_id: int, *, error: str) -> None:
        await self._connection.execute(
            "UPDATE outbox SET state = 'failed', error = ? WHERE id = ?",
            (error, entry_id),
        )


async def ensure_schema(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        await connection.executescript(_SCHEMA)
        await connection.commit()


async def configure_connection(connection: aiosqlite.Connection) -> None:
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA busy_timeout = 5000")


def _row_to_source(row: Any) -> Source:
    return Source(id=row[0], title=row[1], url=row[2], status=row[3])


def _row_to_stored_source(row: Any) -> StoredSource:
    return StoredSource(
        id=row[0],
        owner_id=row[1],
        title=row[2],
        content=row[3],
        url=row[4],
        status=row[5],
    )


def _row_to_match(row: Any, *, score: float) -> PassageMatch:
    return PassageMatch(
        id=row[0],
        source_id=row[1],
        title=row[2],
        text=row[3],
        position=int(row[4]),
        url=row[5],
        score=score,
    )
