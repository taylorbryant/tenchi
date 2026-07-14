"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uv run tenchi dev

The lifespan opens one SQLite connection shared by both repositories; the
context wrapping them is rebuilt per request, and the bearer hook attaches
the authenticated user. Demo tokens: ``alice-token`` and ``bob-token``.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.infra.port_wiring import AppPorts, open_ports
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
async def lifespan() -> AsyncGenerator[AppPorts]:
    async with open_ports(DATABASE_PATH) as ports:
        yield ports


def create_context(ports: AppPorts) -> AppContext:
    return AppContext(projects=ports.projects, tasks=ports.tasks)


app = create_app(
    routes=routes,
    context_factory=create_context,
    lifespan=lifespan,
    hooks=[create_bearer_hook(StaticTokenDirectory(DEMO_TOKENS))],
)
