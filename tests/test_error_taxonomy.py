"""Tenchi's deliberate failures are catchable without catching internals."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ForwardRef

import httpx
import pytest
from pydantic import BaseModel

from tenchi import ConfigurationError, TenchiError
from tenchi.client import Client, UnexpectedResponseError
from tenchi.contracts import contract
from tenchi.errors import AppError
from tenchi.execution import ExecutionError, execute
from tenchi.openapi import openapi_schema
from tenchi.routes import RouteBindingError, route, route_group
from tenchi.server import create_app


class Item(BaseModel):
    name: str


class Unsupported:
    """A type Pydantic cannot validate without arbitrary-types configuration."""


async def get_item(context: object) -> Item:
    return Item(name="x")


def test_named_errors_share_public_roots() -> None:
    assert issubclass(ConfigurationError, TenchiError)
    assert issubclass(ConfigurationError, ValueError)
    assert issubclass(RouteBindingError, ConfigurationError)
    assert issubclass(RouteBindingError, TypeError)
    assert issubclass(ExecutionError, TenchiError)
    assert issubclass(AppError, TenchiError)
    assert issubclass(UnexpectedResponseError, TenchiError)


def test_configuration_surfaces_share_one_catchable_error() -> None:
    with pytest.raises(ConfigurationError, match="unsupported HTTP method"):
        contract(method="FETCH", path="/items")

    with pytest.raises(ConfigurationError, match="prefix must start") as raised:
        route_group(prefix="api")
    assert type(raised.value) is ConfigurationError

    with pytest.raises(ConfigurationError, match="prefix must be a string"):
        route_group(prefix=None)  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match="requires base_url"):
        Client()

    declared = contract(method="GET", path="/items", response=Item)
    duplicate = route_group(route(declared, get_item), route(declared, get_item))

    with pytest.raises(ConfigurationError, match="duplicate route GET /items"):
        create_app(routes=duplicate, context_factory=object)

    with pytest.raises(ConfigurationError, match="duplicate route GET /items"):
        openapi_schema(duplicate, title="Items", version="1")


def test_malformed_contract_values_are_framed_as_configuration() -> None:
    with pytest.raises(ConfigurationError, match="method must be a string"):
        contract(method=None, path="/items")  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match="sunset must be a datetime"):
        contract(method="GET", path="/items", sunset="tomorrow")  # type: ignore[arg-type]

    with pytest.raises(ConfigurationError, match="max_request_bytes must be an int"):
        contract(method="POST", path="/items", request=Item, max_request_bytes=True)

    with pytest.raises(ConfigurationError, match="must be positive"):
        create_app(routes=route_group(), context_factory=object, max_request_bytes=True)

    with pytest.raises(ConfigurationError, match="must be positive"):
        create_app(
            routes=route_group(),
            context_factory=object,
            max_request_bytes="large",  # type: ignore[arg-type]
        )


def test_create_app_rejects_uncallable_context_shapes_at_composition() -> None:
    def keyword_only_context(*, state: object) -> object:
        return state

    with pytest.raises(
        ConfigurationError, match="zero arguments or a single positional"
    ):
        create_app(
            routes=route_group(),
            context_factory=keyword_only_context,  # type: ignore[arg-type]
        )


def test_create_app_rejects_miswired_lifespans_and_hooks() -> None:
    @asynccontextmanager
    async def lifespan(required: object) -> AsyncGenerator[object]:
        yield required

    def hook(info: object) -> None:
        return None

    with pytest.raises(ConfigurationError, match="lifespan must accept zero"):
        create_app(
            routes=route_group(),
            context_factory=object,
            lifespan=lifespan,  # type: ignore[arg-type]
        )

    with pytest.raises(ConfigurationError, match=r"hook\[0\].*two positional"):
        create_app(
            routes=route_group(),
            context_factory=object,
            hooks=(hook,),  # type: ignore[arg-type]
        )


def _unsupported_group():
    declared = contract(method="GET", path="/unsupported", response=Unsupported)

    async def get_unsupported(context: object) -> Unsupported:
        return Unsupported()

    return route_group(route(declared, get_unsupported))


def test_boundary_schema_errors_are_framed_by_the_composing_surface() -> None:
    routes = _unsupported_group()

    with pytest.raises(ConfigurationError, match=r"GET /unsupported.*response type"):
        create_app(routes=routes, context_factory=object)

    with pytest.raises(ConfigurationError, match=r"GET /unsupported.*document"):
        openapi_schema(routes, title="Unsupported", version="1")


async def test_execute_frames_unsupported_annotation_before_context() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def context() -> AsyncGenerator[object]:
        events.append("enter")
        yield object()

    async def use_case(request: Unsupported, context: object) -> None:
        return None

    with pytest.raises(ExecutionError, match=r"cannot validate.*Unsupported"):
        await execute(use_case, request=Unsupported(), context=context)

    assert events == []


async def test_client_frames_an_unsupported_contract_type() -> None:
    routes = _unsupported_group()
    declared = routes.routes[0].contract
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(
            ConfigurationError, match=r"GET /unsupported.*response type"
        ):
            await client.call(declared)

    assert requests == []


def _malformed_schema_group():
    malformed: object = {}
    declared = contract(
        method="GET",
        path="/malformed-schema",
        response=malformed,  # type: ignore[arg-type]
    )

    async def get_malformed(context: object) -> object:
        return {}

    get_malformed.__annotations__["return"] = malformed
    return route_group(route(declared, get_malformed))  # type: ignore[arg-type]


def test_non_pydantic_schema_errors_are_framed_by_composing_surfaces() -> None:
    routes = _malformed_schema_group()

    with pytest.raises(ConfigurationError, match=r"malformed-schema.*response type"):
        create_app(routes=routes, context_factory=object)

    with pytest.raises(ConfigurationError, match=r"malformed-schema.*document"):
        openapi_schema(routes, title="Malformed", version="1")


async def test_execute_frames_non_pydantic_schema_errors_before_context() -> None:
    malformed: object = {}
    events: list[str] = []

    @asynccontextmanager
    async def context() -> AsyncGenerator[object]:
        events.append("enter")
        yield object()

    async def use_case(request: object, context: object) -> None:
        return None

    use_case.__annotations__["request"] = malformed
    with pytest.raises(ExecutionError, match="cannot validate"):
        await execute(use_case, request={}, context=context)

    assert events == []


async def test_client_frames_non_pydantic_schema_errors_before_io() -> None:
    routes = _malformed_schema_group()
    declared = routes.routes[0].contract
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(
            ConfigurationError, match=r"malformed-schema.*response type"
        ):
            await client.call(declared)

    assert requests == []


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("/items/{item_id:nope}", "Unknown path convertor 'nope'"),
        ("/items/{item_id}/{item_id}", "Duplicated param name item_id"),
    ],
)
def test_create_app_frames_invalid_starlette_paths(path: str, message: str) -> None:
    class Params(BaseModel):
        item_id: str

    declared = contract(method="GET", path=path, params=Params)

    async def get_by_id(params: Params, context: object) -> None:
        return None

    with pytest.raises(ConfigurationError, match=message):
        create_app(
            routes=route_group(route(declared, get_by_id)), context_factory=object
        )


def _unresolved_forward_ref_group():
    unresolved = ForwardRef("MissingModel")
    declared = contract(
        method="GET",
        path="/unresolved",
        response=unresolved,  # type: ignore[arg-type]
    )

    async def get_unresolved(context: object) -> object:
        return object()

    get_unresolved.__annotations__["return"] = unresolved
    return route_group(route(declared, get_unresolved))  # type: ignore[arg-type]


def test_create_app_rejects_incomplete_adapters_at_composition() -> None:
    with pytest.raises(ConfigurationError, match=r"unresolved.*response type"):
        create_app(routes=_unresolved_forward_ref_group(), context_factory=object)


@pytest.mark.parametrize("slot", ["query", "headers"])
def test_create_app_rejects_scalar_mapping_input_slots(slot: str) -> None:
    declared = (
        contract(method="GET", path="/scalar", query=int)
        if slot == "query"
        else contract(method="GET", path="/scalar", headers=int)
    )

    async def query_handler(query: int, context: object) -> None:
        return None

    async def headers_handler(headers: int, context: object) -> None:
        return None

    handler = query_handler if slot == "query" else headers_handler
    with pytest.raises(ConfigurationError, match=rf"{slot} type.*object"):
        create_app(routes=route_group(route(declared, handler)), context_factory=object)


async def test_client_rejects_incomplete_adapters_before_io() -> None:
    declared = _unresolved_forward_ref_group().routes[0].contract
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match=r"unresolved.*response type"):
            await client.call(declared)

    assert requests == []
