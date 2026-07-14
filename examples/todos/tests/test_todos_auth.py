"""The API-key hook, composed exactly as app/server/asgi.py wires it."""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.server.hooks import require_api_key
from app.server.routes import routes
from tenchi.server import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    repository = MemoryTodoRepository()
    app = create_app(
        routes=routes,
        context_factory=lambda: AppContext(todos=repository),
        hooks=[require_api_key],
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as http:
        yield http


async def test_requests_are_open_when_no_key_is_configured(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TODOS_API_KEY", raising=False)

    response = await client.get("/todos")

    assert response.status_code == 200


async def test_missing_key_is_rejected_when_configured(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TODOS_API_KEY", "sekrit")

    response = await client.get("/todos")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_valid_key_is_accepted(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TODOS_API_KEY", "sekrit")

    response = await client.get("/todos", headers={"x-api-key": "sekrit"})

    assert response.status_code == 200


async def test_openapi_document_stays_public(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TODOS_API_KEY", "sekrit")

    response = await client.get("/openapi.json")

    assert response.status_code == 200
    # The declared 401 shows up in the document for API routes.
    document = response.json()
    assert "401" in document["paths"]["/todos"]["get"]["responses"]
