from pathlib import Path

import pytest
from tenchi.testing import open_http

from app.infra.port_wiring import ensure_schema, open_todo_repository
from app.server.asgi import build_app
from app.shared.users import OwnerScope

ALICE = {"Authorization": "Bearer alice-token"}


async def test_create_and_list_todos_across_a_restart(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    async with open_http(build_app(database_path)) as http:
        created = await http.post(
            "/todos",
            headers=ALICE,
            json={"title": "Buy milk"},
        )

    assert created.headers["location"] == f"/todos/{created.json()['id']}"

    async with open_http(build_app(database_path)) as http:
        listed = await http.get("/todos", headers=ALICE)

    assert listed.status_code == 200
    assert listed.json() == [created.json()]


async def test_service_routes_are_public(tmp_path: Path) -> None:
    async with open_http(build_app(str(tmp_path / "todos.db"))) as http:
        health = await http.get("/health")
        openapi = await http.get("/openapi.json")
        docs = await http.get("/docs")

    assert health.status_code == 200
    assert openapi.status_code == 200
    assert docs.headers["content-type"] == "text/html; charset=utf-8"


async def test_failed_repository_scope_rolls_back(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    await ensure_schema(database_path)
    owner = OwnerScope(owner_id="alice")

    with pytest.raises(RuntimeError, match="abort request"):
        async with open_todo_repository(database_path) as todos:
            await todos.create(title="Do not persist", owner=owner)
            raise RuntimeError("abort request")

    async with open_todo_repository(database_path) as todos:
        assert await todos.list(owner=owner) == []
