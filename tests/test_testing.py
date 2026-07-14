"""The tenchi.testing helpers drive the app lifespan themselves."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from tenchi.contracts import contract
from tenchi.errors import AppError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_client, open_http


@dataclass(frozen=True, slots=True)
class Context:
    label: str


label_contract = contract(method="GET", path="/label", response=str)
boom = ErrorDef(code="BOOM", status=409, message="Boom")


async def read_label(context: Context) -> str:
    return context.label


def make_lifespan_app(events: list[str]):
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        events.append("startup")
        yield "from-lifespan"
        events.append("shutdown")

    def create_context(state: str) -> Context:
        return Context(label=state)

    return create_app(
        routes=route_group(route(label_contract, read_label)),
        context_factory=create_context,
        lifespan=lifespan,
    )


async def test_open_client_runs_the_lifespan() -> None:
    events: list[str] = []

    async with open_client(make_lifespan_app(events)) as client:
        assert events == ["startup"]
        assert await client.call(label_contract) == "from-lifespan"

    assert events == ["startup", "shutdown"]


async def test_open_http_runs_the_lifespan() -> None:
    events: list[str] = []

    async with open_http(make_lifespan_app(events)) as http:
        response = await http.get("/label")

    assert response.json() == "from-lifespan"
    assert events == ["startup", "shutdown"]


async def test_apps_without_a_lifespan_work_unchanged() -> None:
    app = create_app(
        routes=route_group(route(label_contract, read_label)),
        context_factory=lambda: Context(label="plain"),
    )

    async with open_client(app) as client:
        assert await client.call(label_contract) == "plain"


async def test_headers_and_errors_pass_through() -> None:
    from pydantic import BaseModel

    class WhoHeaders(BaseModel):
        x_caller: str

    who_contract = contract(method="GET", path="/who", headers=WhoHeaders, response=str)
    failing_contract = contract(
        method="GET", path="/fail", response=str, errors=(boom,)
    )

    async def read_caller(headers: WhoHeaders, context: Context) -> str:
        return headers.x_caller

    async def fail(context: Context) -> str:
        raise AppError(boom)

    app = create_app(
        routes=route_group(
            route(who_contract, read_caller), route(failing_contract, fail)
        ),
        context_factory=lambda: Context(label="x"),
    )

    async with open_client(
        app, headers={"x-caller": "tests"}, errors=(boom,)
    ) as client:
        assert await client.call(who_contract) == "tests"
        with pytest.raises(AppError) as excinfo:
            await client.call(failing_contract)

    assert excinfo.value.definition == boom


async def test_startup_failure_raises() -> None:
    @asynccontextmanager
    async def broken_lifespan() -> AsyncGenerator[str]:
        raise RuntimeError("no database")
        yield "unreachable"  # pyright: ignore[reportUnreachable]

    app = create_app(
        routes=route_group(route(label_contract, read_label)),
        context_factory=lambda state: Context(label=state),
        lifespan=broken_lifespan,
    )

    with pytest.raises(RuntimeError, match="no database"):
        async with open_client(app):
            pass
