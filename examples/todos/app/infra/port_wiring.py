"""Wires app ports to concrete adapters for the selected runtime."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.features.todos.ports import TodoRepository

from .sqlite_todo_repository import open_sqlite_todo_repository


@asynccontextmanager
async def open_todo_repository(
    database_path: str,
) -> AsyncGenerator[TodoRepository]:
    async with open_sqlite_todo_repository(database_path) as repository:
        yield repository
