"""The composed app end-to-end: lifespan-owned SQLite repository over HTTP.

Mirrors ``app/server/asgi.py`` with a per-test database path, and simulates a
server restart by composing the app twice against the same file.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette

from app.features.todos.ports import TodoRepository
from app.infra.port_wiring import open_todo_repository
from app.server.context import AppContext
from app.server.routes import routes
from tenchi.server import create_app
from tenchi.testing import open_http


def make_app(database_path: str) -> Starlette:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[TodoRepository]:
        async with open_todo_repository(database_path) as todos:
            yield todos

    def create_context(todos: TodoRepository) -> AppContext:
        return AppContext(todos=todos)

    return create_app(routes=routes, context_factory=create_context, lifespan=lifespan)


async def _request(app: Starlette, method: str, path: str, **kwargs: Any) -> Any:
    async with open_http(app) as client:
        response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


async def test_todos_survive_an_app_restart(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")

    created = await _request(
        make_app(database_path), "POST", "/todos", json={"title": "Buy milk"}
    )

    # A brand-new app instance against the same database sees the todo.
    listed = await _request(make_app(database_path), "GET", "/todos")

    assert listed == [created]
