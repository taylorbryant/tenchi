"""The example's OpenAPI document is served and is valid OpenAPI 3.1."""

from pathlib import Path

import httpx
from openapi_spec_validator import validate

from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.server.routes import OPENAPI_TITLE, OPENAPI_VERSION, api_routes, routes
from tenchi.cli import main
from tenchi.openapi import openapi_schema
from tenchi.server import create_app

SNAPSHOT = Path(__file__).parent.parent / "openapi.json"


def test_document_is_valid_openapi() -> None:
    document = openapi_schema(
        api_routes,
        title=OPENAPI_TITLE,
        version=OPENAPI_VERSION,
    )

    validate(document)

    assert set(document["paths"]) == {"/todos", "/todos/{todo_id}"}
    get_todo = document["paths"]["/todos/{todo_id}"]["get"]
    assert (
        get_todo["responses"]["404"]["description"] == "TODO_NOT_FOUND: Todo not found"
    )


def test_openapi_snapshot_is_current() -> None:
    assert (
        main(
            [
                "openapi",
                "--routes",
                "app.server.routes:api_routes",
                "--title",
                OPENAPI_TITLE,
                "--version",
                OPENAPI_VERSION,
                "--check",
                str(SNAPSHOT),
            ]
        )
        == 0
    )


async def test_document_is_served_by_the_app() -> None:
    app = create_app(
        routes=routes,
        context_factory=lambda: AppContext(todos=MemoryTodoRepository()),
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Todos"
