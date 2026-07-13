from typing import Any

import pytest
from pydantic import BaseModel

from tenchi.contracts import contract
from tenchi.routes import Route, RouteBindingError, route, route_group


class Item(BaseModel):
    name: str


class ItemParams(BaseModel):
    item_id: str


list_contract = contract(method="GET", path="/items", response=list[Item])
create_contract = contract(
    method="POST", path="/items", request=Item, response=Item, status=201
)
get_contract = contract(
    method="GET", path="/items/{item_id}", params=ItemParams, response=Item
)


async def list_items(context: object) -> list[Item]:
    return []


async def create_item(request: Item, context: object) -> Item:
    return request


async def get_item(params: ItemParams, context: object) -> Item:
    return Item(name=params.item_id)


def test_route_computes_call_kwargs_from_contract() -> None:
    assert route(list_contract, list_items).call_kwargs == ("context",)
    assert route(create_contract, create_item).call_kwargs == (
        "request",
        "context",
    )
    assert route(get_contract, get_item).call_kwargs == ("params", "context")

    search_contract = contract(
        method="GET", path="/search", query=ItemParams, response=list[Item]
    )

    async def search_items(query: ItemParams, context: object) -> list[Item]:
        return []

    assert route(search_contract, search_items).call_kwargs == (
        "query",
        "context",
    )


def test_route_rejects_sync_use_case() -> None:
    def sync_use_case(context: object) -> list[Item]:
        return []

    with pytest.raises(RouteBindingError, match="must be an async function"):
        route(list_contract, sync_use_case)  # type: ignore[arg-type]


def test_route_rejects_missing_contract_argument() -> None:
    async def missing_request(context: object) -> Item:
        return Item(name="x")

    with pytest.raises(RouteBindingError, match="must accept a 'request'"):
        route(create_contract, missing_request)


def test_route_rejects_extra_required_parameter() -> None:
    async def needs_more(request: Item, context: object, extra: int) -> Item:
        return request

    with pytest.raises(RouteBindingError, match="required parameter 'extra'"):
        route(create_contract, needs_more)


def test_route_allows_extra_defaulted_parameter_and_kwargs() -> None:
    async def with_default(request: Item, context: object, flag: bool = False) -> Item:
        return request

    async def with_kwargs(**kwargs: Any) -> Item:
        return Item(name="x")

    assert isinstance(route(create_contract, with_default), Route)
    assert isinstance(route(create_contract, with_kwargs), Route)


def test_route_group_flattens_nested_groups() -> None:
    inner = route_group(route(list_contract, list_items))
    outer = route_group(inner, route(create_contract, create_item))

    assert [item.contract.name for item in outer] == [
        "GET /items",
        "POST /items",
    ]


def test_route_group_prefix_rewrites_paths() -> None:
    group = route_group(route(list_contract, list_items), prefix="/api")

    assert [item.contract.path for item in group] == ["/api/items"]
    # The original contract is untouched.
    assert list_contract.path == "/items"


def test_route_group_rejects_relative_prefix() -> None:
    with pytest.raises(ValueError, match="prefix must start with '/'"):
        route_group(route(list_contract, list_items), prefix="api")
