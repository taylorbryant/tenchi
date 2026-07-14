"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uvicorn app.server.asgi:app --reload

The lifespan opens the SQLite-backed repository at startup and closes it at
shutdown; the context wrapping it is rebuilt for every request by
``create_context``.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.features.todos.ports import TodoRepository
from app.infra.port_wiring import open_todo_repository
from app.server.context import AppContext
from app.server.hooks import require_api_key
from app.server.routes import routes
from tenchi.server import create_app

DATABASE_PATH = os.environ.get("TODOS_DATABASE", "todos.db")


@asynccontextmanager
async def lifespan() -> AsyncGenerator[TodoRepository]:
    async with open_todo_repository(DATABASE_PATH) as todos:
        yield todos


def create_context(todos: TodoRepository) -> AppContext:
    return AppContext(todos=todos)


app = create_app(
    routes=routes,
    context_factory=create_context,
    lifespan=lifespan,
    hooks=[require_api_key],
)
