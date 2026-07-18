from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import aiosqlite

from app.features.todos.schemas import Todo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0
)
"""


class SqliteTodoRepository:
    """SQLite implementation of the ``TodoRepository`` port.

    Construct through :func:`open_sqlite_todo_repository`, which owns the
    connection lifecycle.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create(self, *, title: str) -> Todo:
        todo = Todo(id=uuid4().hex, title=title, completed=False)
        await self._connection.execute(
            "INSERT INTO todos (id, title, completed) VALUES (?, ?, ?)",
            (todo.id, todo.title, int(todo.completed)),
        )
        return todo

    async def get(self, todo_id: str) -> Todo | None:
        cursor = await self._connection.execute(
            "SELECT id, title, completed FROM todos WHERE id = ?",
            (todo_id,),
        )
        row = await cursor.fetchone()
        return _row_to_todo(row) if row is not None else None

    async def list(self) -> list[Todo]:
        cursor = await self._connection.execute(
            "SELECT id, title, completed FROM todos ORDER BY rowid"
        )
        rows = await cursor.fetchall()
        return [_row_to_todo(row) for row in rows]


async def ensure_sqlite_todo_schema(database_path: str) -> None:
    """Create the schema once during application startup."""
    async with aiosqlite.connect(database_path) as connection:
        await _configure_connection(connection)
        await connection.execute(_SCHEMA)
        await connection.commit()


@asynccontextmanager
async def open_sqlite_todo_repository(
    database_path: str,
) -> AsyncGenerator[SqliteTodoRepository]:
    """Open one request's transaction and commit or roll it back on exit."""
    async with aiosqlite.connect(database_path) as connection:
        await _configure_connection(connection)
        try:
            yield SqliteTodoRepository(connection)
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise


async def _configure_connection(connection: aiosqlite.Connection) -> None:
    await connection.execute("PRAGMA busy_timeout = 5000")


def _row_to_todo(row: Any) -> Todo:
    return Todo(id=row[0], title=row[1], completed=bool(row[2]))
