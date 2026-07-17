"""Concurrent task retries through HTTP against the transactional adapter."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette

from app.infra.port_wiring import ensure_schema, open_request_ports
from app.infra.static_token_directory import StaticTokenDirectory
from app.server.context import AppContext
from app.server.hooks import create_bearer_hook
from app.server.routes import routes
from app.shared.users import OwnerScope, User
from tenchi.server import create_app
from tenchi.testing import open_http


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
        routes=routes,
        context_factory=create_context,
        lifespan=lifespan,
        hooks=[
            create_bearer_hook(
                StaticTokenDirectory({"alice-token": User(id="alice", name="Alice")})
            )
        ],
    )


async def test_concurrent_http_retries_create_exactly_one_task(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    authorization = {"authorization": "Bearer alice-token"}
    app = make_app(database)

    async with open_http(app) as http:
        project_response = await http.post(
            "/projects", headers=authorization, json={"name": "Launch"}
        )
        project_id = project_response.json()["id"]
        headers = {**authorization, "idempotency-key": "concurrent-http"}
        body = {"project_id": project_id, "title": "Ship it"}

        responses = await asyncio.gather(
            *(http.post("/tasks", headers=headers, json=body) for _ in range(8))
        )

    assert {response.status_code for response in responses} == {201}
    assert {response.text for response in responses} == {responses[0].text}
    assert {response.headers["location"] for response in responses} == {
        responses[0].headers["location"]
    }
    assert {response.headers["etag"] for response in responses} == {'"1"'}

    async with open_request_ports(database) as ports:
        _, total = await ports.task_search.search(
            viewer=OwnerScope(owner_id="alice"),
            project_id=project_id,
            status=None,
            limit=10,
            offset=0,
        )
    assert total == 1
