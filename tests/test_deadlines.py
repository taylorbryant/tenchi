"""Cancellation-safe per-contract request deadlines."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from tenchi.contracts import contract
from tenchi.errors import ERROR_SOURCE_HEADER
from tenchi.openapi import openapi_schema
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http


@dataclass(frozen=True, slots=True)
class Context:
    events: list[str]


async def test_deadline_cancels_work_and_finishes_scope_cleanup_before_504() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(events)
        except BaseException:
            events.append("rollback")
            await asyncio.sleep(0)
            events.append("cleaned")
            raise

    async def slow(context: Context) -> str:
        events.append("use case")
        try:
            await asyncio.sleep(10)
        finally:
            events.append("cancelled")
        return "late"

    declared = contract(
        method="GET",
        path="/slow",
        response=str,
        timeout=0.02,
    )
    app = create_app(
        routes=route_group(route(declared, slow)),
        context_factory=context_factory,
    )

    async with open_http(app) as http:
        response = await http.get("/slow")

    assert response.status_code == 504
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "REQUEST_TIMEOUT"
    assert response.json()["details"] == {"timeout_seconds": 0.02}
    assert events == ["enter", "use case", "cancelled", "rollback", "cleaned"]


async def test_suppressing_deadline_cancellation_cannot_return_a_late_success() -> None:
    events: list[str] = []

    async def suppresses_cancellation(context: object) -> str:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            events.append("suppressed")
            return "late success"
        return "unreachable"

    declared = contract(
        method="GET",
        path="/suppressed",
        response=str,
        timeout=0.01,
    )
    app = create_app(
        routes=route_group(route(declared, suppresses_cancellation)),
        context_factory=object,
    )

    async with open_http(app) as http:
        response = await http.get("/suppressed")

    assert response.status_code == 504
    assert response.json()["code"] == "REQUEST_TIMEOUT"
    assert events == ["suppressed"]


async def test_app_timeout_error_is_not_mistaken_for_deadline_expiry() -> None:
    async def fails(context: object) -> str:
        raise TimeoutError("provider timeout")

    declared = contract(method="GET", path="/fails", response=str, timeout=1.0)
    app = create_app(
        routes=route_group(route(declared, fails)),
        context_factory=object,
    )

    async with open_http(app) as http:
        response = await http.get("/fails")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


def test_deadline_is_documented_in_openapi() -> None:
    async def ok(context: object) -> str:
        return "ok"

    declared = contract(method="GET", path="/timed", response=str, timeout=2.5)
    document = openapi_schema(
        route_group(route(declared, ok)), title="Test", version="1"
    )
    operation = document["paths"]["/timed"]["get"]

    assert operation["x-timeout-seconds"] == 2.5
    assert "REQUEST_TIMEOUT" in operation["responses"]["504"]["description"]
