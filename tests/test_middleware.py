"""The middleware seam passes straight through to Starlette."""

from dataclasses import dataclass

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from tenchi.contracts import contract
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http


@dataclass(frozen=True, slots=True)
class Context:
    pass


ping_contract = contract(method="GET", path="/ping", response=str)


async def ping(context: Context) -> str:
    return "pong"


def make_app(**kwargs: object):
    return create_app(
        routes=route_group(route(ping_contract, ping)),
        context_factory=Context,
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


async def test_cors_preflight_is_answered_by_middleware() -> None:
    app = make_app(
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["https://app.example.com"],
                allow_methods=["*"],
            )
        ]
    )

    async with open_http(app) as http:
        preflight = await http.options(
            "/ping",
            headers={
                "origin": "https://app.example.com",
                "access-control-request-method": "GET",
            },
        )
        response = await http.get(
            "/ping", headers={"origin": "https://app.example.com"}
        )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.example.com"
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"
    assert response.json() == "pong"


async def test_no_middleware_by_default() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/ping", headers={"origin": "https://x.example"})

    assert "access-control-allow-origin" not in response.headers
