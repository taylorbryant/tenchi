"""Request-scoped context factories: enter per request, exit sees errors."""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

import httpx
import pytest
from starlette.applications import Starlette

from tenchi.client import Client
from tenchi.contracts import contract
from tenchi.errors import AppError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import RequestInfo, create_app
from tenchi.testing import open_client


@dataclass(frozen=True, slots=True)
class Context:
    log: list[str]
    user: str | None = None


boom = ErrorDef(code="BOOM", status=409, message="Boom")

ok_contract = contract(method="GET", path="/ok", response=str)
fail_contract = contract(method="GET", path="/fail", response=str, errors=(boom,))
crash_contract = contract(method="GET", path="/crash", response=str)
echo_contract = contract(
    method="POST", path="/echo", request=dict[str, str], response=dict[str, str]
)
wrong_shape_contract = contract(method="GET", path="/wrong-shape", response=str)


async def ok(context: Context) -> str:
    context.log.append("use case")
    return "ok"


async def fail(context: Context) -> str:
    context.log.append("use case")
    raise AppError(boom)


async def crash(context: Context) -> str:
    raise RuntimeError("crash")


async def wrong_shape(context: Context) -> str:
    context.log.append("use case")
    return 42  # type: ignore[return-value]


async def echo(request: dict[str, str], context: Context) -> dict[str, str]:
    return request


def make_app(events: list[str], hooks: list[object] | None = None) -> Starlette:
    @asynccontextmanager
    async def request_context() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(log=events)
            events.append("commit")
        except BaseException:
            events.append("rollback")
            raise

    return create_app(
        routes=route_group(
            route(ok_contract, ok),
            route(fail_contract, fail),
            route(crash_contract, crash),
            route(echo_contract, echo),
            route(wrong_shape_contract, wrong_shape),
        ),
        context_factory=request_context,
        hooks=hooks or [],  # pyright: ignore[reportArgumentType]
    )


@pytest.fixture
async def scope() -> AsyncIterator[tuple[Client, list[str]]]:
    events: list[str] = []
    app = make_app(events)
    async with Client(transport=httpx.ASGITransport(app=app)) as client:
        yield client, events


async def test_success_enters_and_commits(scope: tuple[Client, list[str]]) -> None:
    client, events = scope

    assert await client.call(ok_contract) == "ok"
    assert events == ["enter", "use case", "commit"]


async def test_declared_error_rolls_back_and_still_maps(
    scope: tuple[Client, list[str]],
) -> None:
    client, events = scope

    with pytest.raises(AppError) as excinfo:
        await client.call(fail_contract)

    assert excinfo.value.definition == boom
    assert events == ["enter", "use case", "rollback"]


async def test_unexpected_error_rolls_back(
    scope: tuple[Client, list[str]],
) -> None:
    _, events = scope
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(events)),
        base_url="http://testserver",
    )

    async with http:
        response = await http.get("/crash")

    assert response.status_code == 500
    assert events == ["enter", "rollback"]


async def test_response_contract_violation_rolls_back(
    scope: tuple[Client, list[str]],
) -> None:
    _, events = scope
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(events)),
        base_url="http://testserver",
    )

    async with http:
        response = await http.get("/wrong-shape")

    # The use case's writes must not commit behind the 500.
    assert response.status_code == 500
    assert events == ["enter", "use case", "rollback"]


async def test_validation_failure_exits_cleanly(
    scope: tuple[Client, list[str]],
) -> None:
    _, events = scope
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(events)),
        base_url="http://testserver",
    )

    async with http:
        response = await http.post("/echo", content=b"not json")

    # Nothing ran, so the scope exits without an exception.
    assert response.status_code == 422
    assert events == ["enter", "commit"]


async def test_scope_is_entered_once_per_request() -> None:
    events: list[str] = []
    app = make_app(events)

    async with Client(transport=httpx.ASGITransport(app=app)) as client:
        await client.call(ok_contract)
        await client.call(ok_contract)

    assert events.count("enter") == 2
    assert events.count("commit") == 2


async def test_hook_error_flows_through_the_scope() -> None:
    events: list[str] = []

    def reject(info: RequestInfo, context: Context) -> None:
        raise AppError(boom)

    @asynccontextmanager
    async def request_context() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(log=events)
            events.append("commit")
        except BaseException:
            events.append("rollback")
            raise

    app = create_app(
        routes=route_group(route(ok_contract, ok), errors=(boom,)),
        context_factory=request_context,
        hooks=[reject],
    )

    async with Client(transport=httpx.ASGITransport(app=app), errors=(boom,)) as client:
        with pytest.raises(AppError):
            await client.call(ok_contract)

    assert events == ["enter", "rollback"]


async def test_hook_enrichment_works_inside_the_scope() -> None:
    events: list[str] = []

    def attach(info: RequestInfo, context: Context) -> Context:
        return replace(context, user="alice")

    whoami_contract = contract(method="GET", path="/whoami", response=str)

    async def whoami(context: Context) -> str:
        return context.user or "anonymous"

    @asynccontextmanager
    async def request_context() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context(log=events)
        events.append("commit")

    app = create_app(
        routes=route_group(route(whoami_contract, whoami)),
        context_factory=request_context,
        hooks=[attach],
    )

    async with Client(transport=httpx.ASGITransport(app=app)) as client:
        assert await client.call(whoami_contract) == "alice"

    assert events == ["enter", "commit"]


async def test_request_scope_composes_with_lifespan_state() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        yield "pool"

    @asynccontextmanager
    async def request_context(state: str) -> AsyncGenerator[Context]:
        events.append(f"enter:{state}")
        yield Context(log=events)
        events.append("commit")

    app = create_app(
        routes=route_group(route(ok_contract, ok)),
        context_factory=request_context,
        lifespan=lifespan,
    )

    async with open_client(app) as client:
        assert await client.call(ok_contract) == "ok"

    assert events == ["enter:pool", "use case", "commit"]


async def test_context_factory_raising_declared_error_maps_to_it() -> None:
    def refuses() -> Context:
        raise AppError(boom)

    app = create_app(
        routes=route_group(route(ok_contract, ok), errors=(boom,)),
        context_factory=refuses,
    )

    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    async with http:
        response = await http.get("/ok")

    assert response.status_code == 409
    assert response.json()["code"] == "BOOM"


async def test_state_taking_factory_without_lifespan_run_is_a_500() -> None:
    def needs_state(state: str) -> Context:
        return Context(log=[])

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        yield "pool"

    app = create_app(
        routes=route_group(route(ok_contract, ok)),
        context_factory=needs_state,
        lifespan=lifespan,
    )

    # Call without driving the lifespan: state was never populated.
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    async with http:
        response = await http.get("/ok")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"
