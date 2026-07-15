"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uv run tenchi dev

The lifespan ensures the schema once at startup; each request gets its own
connection and transaction via the request-scoped context factory —
committed when the use case succeeds, rolled back when it raises. The
bearer hook attaches the authenticated user. Demo tokens: ``alice-token``
and ``bob-token``.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.infra.port_wiring import ensure_schema, open_request_ports
from app.infra.static_token_directory import StaticTokenDirectory
from app.server.context import AppContext
from app.server.hooks import create_bearer_hook
from app.server.routes import routes
from app.shared.users import User
from tenchi.server import create_app

DATABASE_PATH = os.environ.get("TASKBOARD_DATABASE", "taskboard.db")

DEMO_TOKENS = {
    "alice-token": User(id="alice", name="Alice"),
    "bob-token": User(id="bob", name="Bob"),
}


@asynccontextmanager
async def lifespan() -> AsyncGenerator[str]:
    await ensure_schema(DATABASE_PATH)
    yield DATABASE_PATH


@asynccontextmanager
async def create_context(database_path: str) -> AsyncGenerator[AppContext]:
    async with open_request_ports(database_path) as ports:
        yield AppContext(
            projects=ports.projects,
            tasks=ports.tasks,
            task_search=ports.task_search,
            outbox=ports.outbox,
            notifications=ports.notifications,
        )


app = create_app(
    routes=routes,
    context_factory=create_context,
    lifespan=lifespan,
    hooks=[create_bearer_hook(StaticTokenDirectory(DEMO_TOKENS))],
)
