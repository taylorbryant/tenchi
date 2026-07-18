"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uvicorn app.server.asgi:app --reload

The lifespan ensures the SQLite schema at startup. Each request opens its own
connection and transaction through ``create_context`` so concurrent requests
cannot observe or commit each other's in-flight writes.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.infra.port_wiring import ensure_schema, open_todo_repository
from app.server.context import AppContext
from app.server.hooks import require_api_key
from app.server.routes import routes
from tenchi.server import create_app

DATABASE_PATH = os.environ.get("TODOS_DATABASE", "todos.db")


@asynccontextmanager
async def lifespan() -> AsyncGenerator[str]:
    await ensure_schema(DATABASE_PATH)
    yield DATABASE_PATH


@asynccontextmanager
async def create_context(database_path: str) -> AsyncGenerator[AppContext]:
    async with open_todo_repository(database_path) as todos:
        yield AppContext(todos=todos)


app = create_app(
    routes=routes,
    context_factory=create_context,
    lifespan=lifespan,
    hooks=[require_api_key],
)
