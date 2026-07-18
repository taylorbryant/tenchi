"""Wires app ports to concrete adapters for the selected runtime."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.features.todos.ports import TodoRepository

from .sqlite_todo_repository import (
    ensure_sqlite_todo_schema,
    open_sqlite_todo_repository,
)


async def ensure_schema(database_path: str) -> None:
    await ensure_sqlite_todo_schema(database_path)


@asynccontextmanager
async def open_todo_repository(
    database_path: str,
) -> AsyncGenerator[TodoRepository]:
    async with open_sqlite_todo_repository(database_path) as repository:
        yield repository
