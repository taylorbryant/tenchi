from pathlib import Path

import pytest
from tenchi.testing import open_client, open_http

from app.features.todos.contracts import create_todo_contract
from app.features.todos.schemas import CreateTodo, CreateTodoHeaders
from app.infra.port_wiring import ensure_schema, open_todo_repository
from app.server.asgi import build_app


async def test_create_and_list_todos_across_a_restart(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    async with open_client(build_app(database_path)) as client:
        created = await client.call_with_response(
            create_todo_contract,
            headers=CreateTodoHeaders(idempotency_key="create-001"),
            request=CreateTodo(title="Buy milk"),
        )

    assert created.headers.location == f"/todos/{created.body.id}"

    async with open_http(build_app(database_path)) as http:
        listed = await http.get("/todos")

    assert listed.status_code == 200
    assert listed.json() == [created.body.model_dump()]


async def test_create_requires_the_published_idempotency_key(tmp_path: Path) -> None:
    async with open_http(build_app(str(tmp_path / "todos.db"))) as http:
        missing = await http.post("/todos", json={"title": "No key"})

    assert missing.status_code == 422


async def test_service_routes_are_available(tmp_path: Path) -> None:
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

    with pytest.raises(RuntimeError, match="abort request"):
        async with open_todo_repository(database_path) as todos:
            await todos.create(title="Do not persist")
            raise RuntimeError("abort request")

    async with open_todo_repository(database_path) as todos:
        assert await todos.list() == []
