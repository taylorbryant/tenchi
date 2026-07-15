"""Per-request transactions through the full HTTP stack.

Composes an app exactly like ``app/server/asgi.py`` (request-scoped
connection, commit on success, rollback on error) with one deliberately
failing route that writes before raising, and one that writes and
succeeds.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette

from app.features.projects.schemas import Project
from app.infra.port_wiring import ensure_schema, open_request_ports
from app.server.context import AppContext
from app.shared.users import OwnerScope
from tenchi.contracts import contract
from tenchi.errors import AppError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http

glitch = ErrorDef(code="GLITCH", status=409, message="Glitched after writing")

write_contract = contract(method="POST", path="/write", response=Project, status=201)
glitch_contract = contract(
    method="POST", path="/glitch", response=Project, errors=(glitch,)
)


async def write_project(context: AppContext) -> Project:
    return await context.projects.create(
        name="kept", owner=OwnerScope(owner_id="alice")
    )


async def write_then_fail(context: AppContext) -> Project:
    await context.projects.create(name="doomed", owner=OwnerScope(owner_id="alice"))
    raise AppError(glitch)


def make_app(database_path: str) -> Starlette:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        await ensure_schema(database_path)
        yield database_path

    @asynccontextmanager
    async def create_context(path: str) -> AsyncGenerator[AppContext]:
        async with open_request_ports(path) as ports:
            yield AppContext(
                projects=ports.projects,
                tasks=ports.tasks,
                task_search=ports.task_search,
                outbox=ports.outbox,
                notifications=ports.notifications,
            )

    return create_app(
        routes=route_group(
            route(write_contract, write_project),
            route(glitch_contract, write_then_fail),
        ),
        context_factory=create_context,
        lifespan=lifespan,
    )


async def test_commit_on_success_rollback_on_error(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    app = make_app(database)

    async with open_http(app) as http:
        kept = await http.post("/write")
        assert kept.status_code == 201

        failed = await http.post("/glitch")
        assert failed.status_code == 409
        assert failed.json()["code"] == "GLITCH"

    async with open_request_ports(database) as ports:
        names = [
            p.name
            for p in await ports.projects.list_owned_by(OwnerScope(owner_id="alice"))
        ]

    # The successful request committed; the failed one rolled back.
    assert names == ["kept"]
