"""HTTP integration tests for the todos example.

Each test composes a fresh application with its own repository, mirroring
``app/server/asgi.py`` but with per-test isolation.
"""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.server.routes import routes
from tenchi.errors import ERROR_SOURCE_HEADER
from tenchi.server import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    repository = MemoryTodoRepository()
    app = create_app(
        routes=routes,
        context_factory=lambda: AppContext(todos=repository),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


async def test_create_todo_validates_and_returns_201(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/todos", json={"title": "Buy milk"})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Buy milk"
    assert body["completed"] is False
    assert isinstance(body["id"], str) and body["id"]


async def test_create_todo_rejects_invalid_body(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/todos", json={})

    assert response.status_code == 422
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["title"]


async def test_create_todo_rejects_malformed_json(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post(
        "/todos",
        content=b"not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_list_todos_returns_created_todos(
    client: httpx.AsyncClient,
) -> None:
    created = (await client.post("/todos", json={"title": "one"})).json()

    response = await client.get("/todos")

    assert response.status_code == 200
    assert response.json() == [created]


async def test_list_todos_filters_by_query(client: httpx.AsyncClient) -> None:
    created = (await client.post("/todos", json={"title": "one"})).json()

    open_todos = await client.get("/todos", params={"completed": "false"})
    done_todos = await client.get("/todos", params={"completed": "true"})

    assert open_todos.json() == [created]
    assert done_todos.json() == []


async def test_list_todos_rejects_invalid_query(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/todos", params={"completed": "banana"})

    assert response.status_code == 422
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_get_todo_returns_todo_by_path_param(
    client: httpx.AsyncClient,
) -> None:
    created = (await client.post("/todos", json={"title": "one"})).json()

    response = await client.get(f"/todos/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_get_todo_maps_expected_error(client: httpx.AsyncClient) -> None:
    response = await client.get("/todos/missing")

    assert response.status_code == 404
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    assert response.json() == {
        "code": "TODO_NOT_FOUND",
        "message": "Todo not found",
        "details": {"todo_id": "missing"},
    }


async def test_health_endpoint_is_live(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_unknown_route_is_a_framework_404(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/nope")

    assert response.status_code == 404
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "NOT_FOUND"


async def test_wrong_method_is_a_framework_405(
    client: httpx.AsyncClient,
) -> None:
    response = await client.delete("/todos")

    assert response.status_code == 405
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
