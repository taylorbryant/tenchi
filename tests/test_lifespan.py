"""Lifespan-managed resources flowing into the request context."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
import pytest
from starlette.applications import Starlette

from tenchi.contracts import contract
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http


@dataclass(frozen=True, slots=True)
class Context:
    label: str


label_contract = contract(method="GET", path="/label", response=str)


async def read_label(context: Context) -> str:
    return context.label


def make_app(events: list[str]) -> Starlette:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        events.append("startup")
        try:
            yield "from-lifespan"
        finally:
            events.append("shutdown")

    def create_context(state: str) -> Context:
        return Context(label=state)

    return create_app(
        routes=route_group(route(label_contract, read_label)),
        context_factory=create_context,
        lifespan=lifespan,
    )


async def test_lifespan_state_reaches_the_context_factory() -> None:
    events: list[str] = []
    app = make_app(events)

    async with open_http(app) as client:
        response = await client.get("/label")

    assert response.json() == "from-lifespan"
    assert events == ["startup", "shutdown"]


async def test_zero_arg_factory_may_still_use_a_lifespan() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[None]:
        events.append("startup")
        yield
        events.append("shutdown")

    app = create_app(
        routes=route_group(route(label_contract, read_label)),
        context_factory=lambda: Context(label="module-scoped"),
        lifespan=lifespan,
    )

    async with open_http(app) as client:
        response = await client.get("/label")

    assert response.json() == "module-scoped"
    assert events == ["startup", "shutdown"]


def test_state_factory_without_lifespan_is_rejected() -> None:
    with pytest.raises(ValueError, match="no lifespan= was provided"):
        create_app(
            routes=route_group(route(label_contract, read_label)),
            context_factory=lambda state: Context(label=state),  # type: ignore[arg-type]
        )


def test_factory_with_two_required_arguments_is_rejected() -> None:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        yield "x"

    with pytest.raises(ValueError, match="zero arguments or a single"):
        create_app(
            routes=route_group(route(label_contract, read_label)),
            context_factory=lambda state, extra: Context(label=state),  # type: ignore[arg-type,misc]
            lifespan=lifespan,
        )


async def test_state_request_without_running_lifespan_is_a_500() -> None:
    app = make_app([])

    # No LifespanManager: the transport never runs startup, so the state is
    # unavailable and the framework reports an internal error.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/label")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
