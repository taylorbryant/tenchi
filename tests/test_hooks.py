"""The application hook seam: authenticate, reject, and enrich context."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import httpx
import pytest
from pydantic import BaseModel
from starlette.applications import Starlette

from tenchi.contracts import contract
from tenchi.errors import ERROR_SOURCE_HEADER, AppError, ErrorDef
from tenchi.routes import RouteGroup, route, route_group
from tenchi.server import RequestInfo, create_app


class Item(BaseModel):
    name: str


@dataclass(frozen=True, slots=True)
class Context:
    user: str | None = None


unauthorized = ErrorDef(code="UNAUTHORIZED", status=401, message="Unauthorized")

whoami_contract = contract(method="GET", path="/whoami", response=str, tags=("health",))
echo_contract = contract(method="POST", path="/echo", request=Item, response=Item)
public_contract = contract(method="GET", path="/health", response=str, public=True)


async def whoami(context: Context) -> str:
    return context.user or "anonymous"


async def echo(request: Item, context: Context) -> Item:
    return request


async def health(context: Context) -> str:
    return "ok"


def make_routes(*, declare_errors: bool = True) -> RouteGroup:
    return route_group(
        route(whoami_contract, whoami),
        route(echo_contract, echo),
        route(public_contract, health),
        errors=(unauthorized,) if declare_errors else (),
    )


def api_key_hook(info: RequestInfo, context: Context) -> Context | None:
    if info.contract.public:
        return None
    key = info.headers.get("x-api-key")
    if key != "secret":
        raise AppError(unauthorized)
    return replace(context, user=f"key:{key}")


def make_client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        routes=make_routes(),
        context_factory=Context,
        hooks=[api_key_hook],
    )
    async with make_client(app) as http:
        yield http


async def test_hook_rejects_private_route_with_public_looking_tag(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/whoami")

    assert response.status_code == 401
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_hook_enriches_the_context(client: httpx.AsyncClient) -> None:
    response = await client.get("/whoami", headers={"x-api-key": "secret"})

    assert response.status_code == 200
    assert response.json() == "key:secret"


async def test_hook_rejection_wins_over_validation(
    client: httpx.AsyncClient,
) -> None:
    # Invalid body AND missing key: authentication is checked first.
    response = await client.post("/echo", json={})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_hook_exempts_routes_via_contract_metadata(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == "ok"


async def test_undeclared_hook_error_becomes_internal_error() -> None:
    app = create_app(
        routes=make_routes(declare_errors=False),
        context_factory=Context,
        hooks=[api_key_hook],
    )

    async with make_client(app) as http:
        response = await http.get("/whoami")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"


async def test_hooks_run_in_order_and_may_be_async() -> None:
    def first(info: RequestInfo, context: Context) -> Context:
        return replace(context, user="first")

    async def second(info: RequestInfo, context: Context) -> Context:
        return replace(context, user=f"{context.user},second")

    app = create_app(
        routes=make_routes(),
        context_factory=Context,
        hooks=[first, second],
    )

    async with make_client(app) as http:
        response = await http.get("/whoami")

    assert response.json() == "first,second"


async def test_hook_sees_method_path_and_lowercased_headers() -> None:
    seen: list[RequestInfo] = []

    def observe(info: RequestInfo, context: Context) -> None:
        seen.append(info)

    app = create_app(routes=make_routes(), context_factory=Context, hooks=[observe])

    async with make_client(app) as http:
        await http.get("/whoami", headers={"X-Custom-Header": "value"})

    assert seen[0].method == "GET"
    assert seen[0].path == "/whoami"
    assert seen[0].headers["x-custom-header"] == "value"
    assert seen[0].contract.name == "GET /whoami"


async def test_crashing_hook_becomes_internal_error() -> None:
    def broken(info: RequestInfo, context: Context) -> None:
        raise RuntimeError("boom")

    app = create_app(routes=make_routes(), context_factory=Context, hooks=[broken])

    async with make_client(app) as http:
        response = await http.get("/whoami")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
