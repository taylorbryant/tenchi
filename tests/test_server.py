"""Framework-level HTTP behavior, exercised through a minimal inline app."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from pydantic import BaseModel

from tenchi.contracts import contract
from tenchi.errors import ERROR_SOURCE_HEADER, AppError, ErrorDef
from tenchi.routes import RouteGroup, route, route_group
from tenchi.server import create_app


class Item(BaseModel):
    name: str


@dataclass(frozen=True, slots=True)
class Context:
    request_id: int


boom = ErrorDef(code="BOOM", status=409, message="Boom")


async def make_client(routes: RouteGroup) -> httpx.AsyncClient:
    counter = iter(range(1_000_000))

    async def context_factory() -> Context:
        return Context(request_id=next(counter))

    app = create_app(routes=routes, context_factory=context_factory)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async def echo(request: Item, context: Context) -> Item:
        return request

    async def no_content(context: Context) -> None:
        return None

    async def declared_error(context: Context) -> Item:
        raise AppError(boom, details={"why": "declared"})

    async def undeclared_error(context: Context) -> Item:
        raise AppError(boom)

    async def crash(context: Context) -> Item:
        raise RuntimeError("crash")

    async def wrong_shape(context: Context) -> Item:
        return "not an item"  # type: ignore[return-value]

    async def whoami(context: Context) -> int:
        return context.request_id

    routes = route_group(
        route(
            contract(method="POST", path="/echo", request=Item, response=Item),
            echo,
        ),
        route(
            contract(method="DELETE", path="/empty", status=204),
            no_content,
        ),
        route(
            contract(method="POST", path="/declared", response=Item, errors=(boom,)),
            declared_error,
        ),
        route(
            contract(method="POST", path="/undeclared", response=Item),
            undeclared_error,
        ),
        route(
            contract(method="POST", path="/crash", response=Item),
            crash,
        ),
        route(
            contract(method="GET", path="/wrong-shape", response=Item),
            wrong_shape,
        ),
        route(
            contract(method="GET", path="/whoami", response=int),
            whoami,
        ),
    )
    async with await make_client(routes) as client:
        yield client


async def test_dispatch_validates_and_echoes(client: httpx.AsyncClient) -> None:
    response = await client.post("/echo", json={"name": "x"})

    assert response.status_code == 200
    assert response.json() == {"name": "x"}
    assert ERROR_SOURCE_HEADER not in response.headers


async def test_invalid_body_maps_to_framework_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/echo", json={"name": 1})

    assert response.status_code == 422
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_contract_without_response_returns_empty_body(
    client: httpx.AsyncClient,
) -> None:
    response = await client.delete("/empty")

    assert response.status_code == 204
    assert response.content == b""


async def test_declared_error_maps_to_its_status(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/declared")

    assert response.status_code == 409
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    assert response.json() == {
        "code": "BOOM",
        "message": "Boom",
        "details": {"why": "declared"},
    }


async def test_undeclared_app_error_becomes_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/undeclared")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_unexpected_exception_becomes_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/crash")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_response_not_matching_contract_becomes_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/wrong-shape")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_context_factory_runs_per_request(
    client: httpx.AsyncClient,
) -> None:
    first = await client.get("/whoami")
    second = await client.get("/whoami")

    assert first.json() != second.json()


async def test_create_app_rejects_duplicate_routes() -> None:
    async def use_case(context: Context) -> Item:
        return Item(name="x")

    declared = contract(method="GET", path="/dup", response=Item)
    routes = route_group(route(declared, use_case), route(declared, use_case))

    with pytest.raises(ValueError, match="duplicate route GET /dup"):
        create_app(routes=routes, context_factory=lambda: Context(request_id=0))
