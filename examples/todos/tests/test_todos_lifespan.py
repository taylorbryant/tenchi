"""The composed app end-to-end: request-scoped SQLite transactions over HTTP.

Mirrors ``app/server/asgi.py`` with a per-test database path, and simulates a
server restart by composing the app twice against the same file.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette

from app.infra.port_wiring import ensure_schema, open_todo_repository
from app.server.context import AppContext
from app.server.routes import routes
from tenchi.server import create_app
from tenchi.testing import open_http


def make_app(database_path: str) -> Starlette:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        await ensure_schema(database_path)
        yield database_path

    @asynccontextmanager
    async def create_context(path: str) -> AsyncGenerator[AppContext]:
        async with open_todo_repository(path) as todos:
            yield AppContext(todos=todos)

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
