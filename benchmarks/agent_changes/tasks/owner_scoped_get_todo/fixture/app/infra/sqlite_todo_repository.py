from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import aiosqlite

from app.features.todos.schemas import Todo
from app.shared.users import OwnerScope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    owner_id TEXT
)
"""


class SqliteTodoRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create(self, *, title: str, owner: OwnerScope) -> Todo:
        todo = Todo(id=uuid4().hex, title=title, completed=False)
        await self._connection.execute(
            "INSERT INTO todos (id, title, completed, owner_id) VALUES (?, ?, ?, ?)",
            (todo.id, todo.title, int(todo.completed), owner.owner_id),
        )
        return todo

    async def list(self, *, owner: OwnerScope) -> list[Todo]:
        cursor = await self._connection.execute(
            "SELECT id, title, completed FROM todos WHERE owner_id = ? ORDER BY rowid",
            (owner.owner_id,),
        )
        return [_row_to_todo(row) for row in await cursor.fetchall()]


async def ensure_sqlite_todo_schema(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as connection:
        await _configure_connection(connection)
        await connection.execute(_SCHEMA)
        columns = {
            str(row[1])
            for row in await (
                await connection.execute("PRAGMA table_info(todos)")
            ).fetchall()
        }
        if "owner_id" not in columns:
            await connection.execute("ALTER TABLE todos ADD COLUMN owner_id TEXT")
        await connection.commit()


@asynccontextmanager
async def open_sqlite_todo_repository(
    database_path: str,
) -> AsyncGenerator[SqliteTodoRepository]:
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
