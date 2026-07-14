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


async def test_body_exactly_at_the_limit_is_accepted() -> None:
    """Both checks use strict >, so the limit itself is inclusive."""
    body = b'{"name": "' + b"x" * 88 + b'"}'  # exactly 100 bytes
    assert len(body) == 100

    async with open_http(make_app(max_request_bytes=100)) as http:
        response = await http.post(
            "/echo", content=body, headers={"content-type": "application/json"}
        )

    assert response.status_code == 200


async def test_contract_ceiling_applies_when_app_default_is_disabled() -> None:
    async with open_http(make_app(max_request_bytes=None)) as http:
        accepted = await http.post("/upload", content=b"x" * 150)
        rejected = await http.post("/upload", content=b"x" * 250)

    assert accepted.status_code == 200
    assert rejected.status_code == 413


async def test_contract_ceiling_smaller_than_app_default_wins() -> None:
    tight = contract(
        method="POST",
        path="/tight",
        request=bytes,
        request_media_type="application/octet-stream",
        response=int,
        max_request_bytes=10,
    )
    app = create_app(
        routes=route_group(route(tight, swallow)),
        context_factory=Context,
        max_request_bytes=10_000,
    )

    async with open_http(app) as http:
        response = await http.post("/tight", content=b"x" * 50)

    assert response.status_code == 413
    assert response.json()["details"] == {"limit_bytes": 10}


async def test_malformed_content_length_falls_back_to_stream_counting() -> None:
    """Unicode digits pass isdigit() but crash int(); the parser must
    shrug and let the counted stream enforce the cap."""
    from tenchi.server import (
        _declared_content_length,  # pyright: ignore[reportPrivateUsage]
    )

    assert _declared_content_length("²") is None
    assert _declared_content_length("①23") is None
    assert _declared_content_length("-5") is None
    assert _declared_content_length("+10") is None
    assert _declared_content_length(" 10") is None
    assert _declared_content_length("9" * 30) is None
    assert _declared_content_length("150") == 150
    assert _declared_content_length(None) is None


async def test_client_disconnect_mid_body_is_not_an_error() -> None:
    """An abandoned upload logs at info and rolls back — never a 500."""
    events: list[str] = []

    @asynccontextmanager
    async def request_context() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context()
            events.append("commit")
        except BaseException:
            events.append("rollback")
            raise

    app = create_app(
        routes=route_group(route(echo_contract, echo)),
        context_factory=request_context,
        max_request_bytes=10_000,
    )

    from typing import Any

    messages: list[dict[str, Any]] = [
        {"type": "http.request", "body": b'{"na', "more_body": True},
        {"type": "http.disconnect"},
    ]
    pending = iter(messages)

    async def receive() -> dict[str, Any]:
        return next(pending)

    sent: list[dict[str, Any]] = []

    async def send(message: Any) -> None:
        sent.append(dict(message))

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/echo",
        "raw_path": b"/echo",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "asgi": {"version": "3.0"},
    }
    await app(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 499
    assert events == ["enter", "rollback"]


def test_create_app_rejects_nonpositive_body_cap() -> None:
    with pytest.raises(ValueError, match="max_request_bytes must be positive"):
        create_app(
            routes=route_group(route(echo_contract, echo)),
            context_factory=Context,
            max_request_bytes=0,
        )


def test_contract_rejects_body_cap_without_request_type() -> None:
    with pytest.raises(ValueError, match="declares no request type"):
        contract(method="GET", path="/x", response=str, max_request_bytes=10)


def test_contract_rejects_naive_deprecated_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        contract(method="GET", path="/x", response=str, deprecated=datetime(2026, 6, 1))


async def test_deprecated_datetime_sends_rfc9745_header() -> None:
    when = datetime(2026, 6, 1, tzinfo=UTC)
    dated = contract(method="GET", path="/dated", response=str, deprecated=when)
    app = create_app(routes=route_group(route(dated, ping)), context_factory=Context)

    async with open_http(app) as http:
        response = await http.get("/dated")

    assert response.headers["deprecation"] == f"@{int(when.timestamp())}"
