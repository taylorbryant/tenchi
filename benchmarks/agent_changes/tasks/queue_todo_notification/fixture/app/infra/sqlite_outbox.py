from dataclasses import dataclass

import aiosqlite

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    payload TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
)
"""


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    id: int
    job: str
    payload_json: str


class SqliteOutbox:
    """Transactional outbox over the request-scoped write connection."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def enqueue(self, *, job: str, payload_json: bytes) -> None:
        await self._connection.execute(
            "INSERT INTO outbox (job, payload) VALUES (?, ?)",
            (job, payload_json.decode("utf-8")),
        )

    async def list_pending(self) -> list[OutboxEntry]:
        cursor = await self._connection.execute(
            "SELECT id, job, payload FROM outbox WHERE processed = 0 ORDER BY id"
        )
        return [
            OutboxEntry(id=int(row[0]), job=str(row[1]), payload_json=str(row[2]))
            for row in await cursor.fetchall()
        ]
