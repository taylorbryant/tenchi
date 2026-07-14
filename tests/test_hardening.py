"""Boundary hardening: body size limits and lifecycle headers."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel

from tenchi.contracts import contract
from tenchi.errors import ERROR_SOURCE_HEADER
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http


class Item(BaseModel):
    name: str


@dataclass(frozen=True, slots=True)
class Context:
    pass


async def echo(request: Item, context: Context) -> Item:
    return request


async def swallow(request: bytes, context: Context) -> int:
    return len(request)


async def ping(context: Context) -> str:
    return "pong"


SUNSET = datetime(2027, 1, 1, tzinfo=UTC)

echo_contract = contract(method="POST", path="/echo", request=Item, response=Item)
upload_contract = contract(
    method="POST",
    path="/upload",
    request=bytes,
    request_media_type="application/octet-stream",
    response=int,
    max_request_bytes=200,
)
old_contract = contract(
    method="GET",
    path="/old",
    response=str,
    deprecated=True,
    sunset=SUNSET,
)


def make_app(max_request_bytes: int | None = 100):
    return create_app(
        routes=route_group(
            route(echo_contract, echo),
            route(upload_contract, swallow),
            route(old_contract, ping),
        ),
        context_factory=Context,
        max_request_bytes=max_request_bytes,
    )


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with open_http(make_app()) as client:
        yield client


async def test_oversized_body_is_a_framework_413(http: httpx.AsyncClient) -> None:
    response = await http.post("/echo", json={"name": "x" * 200})

    assert response.status_code == 413
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    body = response.json()
    assert body["code"] == "REQUEST_TOO_LARGE"
    assert body["details"] == {"limit_bytes": 100}
    assert body["request_id"] == response.headers["x-request-id"]


async def test_bodies_within_the_limit_pass(http: httpx.AsyncClient) -> None:
    response = await http.post("/echo", json={"name": "small"})

    assert response.status_code == 200


async def test_chunked_bodies_without_content_length_are_capped(
    http: httpx.AsyncClient,
) -> None:
    async def chunks() -> AsyncGenerator[bytes]:
        for _ in range(50):
            yield b"x" * 10  # 500 bytes total, no content-length

    response = await http.post(
        "/echo", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json()["code"] == "REQUEST_TOO_LARGE"


async def test_contract_ceiling_overrides_the_app_default(
    http: httpx.AsyncClient,
) -> None:
    accepted = await http.post("/upload", content=b"x" * 150)
    rejected = await http.post("/upload", content=b"x" * 250)

    assert accepted.status_code == 200
    assert accepted.json() == 150
    assert rejected.status_code == 413


async def test_disabling_the_app_default_removes_the_cap() -> None:
    async with open_http(make_app(max_request_bytes=None)) as http:
        response = await http.post("/echo", json={"name": "x" * 5000})

    assert response.status_code == 200


async def test_oversized_body_exits_a_request_scope_cleanly() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def request_context() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context()
        events.append("commit")

    app = create_app(
        routes=route_group(route(echo_contract, echo)),
        context_factory=request_context,
        max_request_bytes=100,
    )
    async with open_http(app) as http:
        response = await http.post("/echo", json={"name": "x" * 200})

    assert response.status_code == 413
    assert events == ["enter", "commit"]


async def test_deprecated_routes_send_lifecycle_headers(
    http: httpx.AsyncClient,
) -> None:
    response = await http.get("/old")

    assert response.status_code == 200
    assert response.headers["deprecation"] == "true"
    assert response.headers["sunset"] == "Fri, 01 Jan 2027 00:00:00 GMT"


async def test_lifecycle_headers_appear_on_error_responses_too() -> None:
    app = create_app(
        routes=route_group(
            route(
                contract(
                    method="POST",
                    path="/old-echo",
                    request=Item,
                    response=Item,
                    deprecated=True,
                ),
                echo,
            )
        ),
        context_factory=Context,
    )
    async with open_http(app) as http:
        response = await http.post("/old-echo", json={})

    assert response.status_code == 422
    assert response.headers["deprecation"] == "true"


async def test_current_routes_send_no_lifecycle_headers(
    http: httpx.AsyncClient,
) -> None:
    response = await http.post("/echo", json={"name": "x"})

    assert "deprecation" not in response.headers
    assert "sunset" not in response.headers


def test_contract_rejects_naive_sunset() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        contract(method="GET", path="/x", response=str, sunset=datetime(2027, 1, 1))


def test_contract_rejects_nonpositive_body_limit() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        contract(method="POST", path="/x", request=Item, max_request_bytes=0)
