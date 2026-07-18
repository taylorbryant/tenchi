"""The SQLite adapter satisfies the TodoRepository port against a real file."""

from pathlib import Path

import pytest

from app.infra.sqlite_todo_repository import (
    ensure_sqlite_todo_schema,
    open_sqlite_todo_repository,
)


async def test_create_get_and_list(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    await ensure_sqlite_todo_schema(database_path)

    async with open_sqlite_todo_repository(database_path) as repository:
        created = await repository.create(title="Buy milk")

        assert created.title == "Buy milk"
        assert created.completed is False
        assert await repository.get(created.id) == created
        assert await repository.get("missing") is None
        assert await repository.list() == [created]


async def test_todos_persist_across_connections(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    await ensure_sqlite_todo_schema(database_path)

    async with open_sqlite_todo_repository(database_path) as repository:
        created = await repository.create(title="survive restart")

    async with open_sqlite_todo_repository(database_path) as repository:
        assert await repository.list() == [created]


async def test_failed_scope_rolls_back(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    await ensure_sqlite_todo_schema(database_path)

    with pytest.raises(RuntimeError, match="abort request"):
        async with open_sqlite_todo_repository(database_path) as repository:
            await repository.create(title="Do not persist")
            raise RuntimeError("abort request")

    async with open_sqlite_todo_repository(database_path) as repository:
        assert await repository.list() == []
