from typing import Any

import pytest
from pydantic import BaseModel

from tenchi.contracts import contract
from tenchi.errors import ConfigurationError, ErrorDef
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


def test_route_allows_extra_defaulted_parameter_and_context_through_kwargs() -> None:
    async def with_default(request: Item, context: object, flag: bool = False) -> Item:
        return request

    async def with_kwargs(**kwargs: Any) -> list[Item]:
        assert "context" in kwargs
        return []

    assert isinstance(route(create_contract, with_default), Route)
    assert isinstance(route(list_contract, with_kwargs), Route)


def test_route_requires_declared_inputs_to_be_explicit_parameters() -> None:
    async def with_kwargs(**kwargs: Any) -> Item:
        return Item(name=str(kwargs["request"]))

    with pytest.raises(RouteBindingError, match="explicit annotated 'request'"):
        route(create_contract, with_kwargs)


@pytest.mark.parametrize("argument", ["params", "query", "headers", "request"])
def test_route_rejects_boundary_annotation_mismatches(argument: str) -> None:
    declared = contract(
        method="POST",
        path="/items/{item_id}",
        params=ItemParams,
        query=ItemParams,
        headers=ItemParams,
        request=Item,
        response=Item,
    )

    async def handler(
        params: ItemParams,
        query: ItemParams,
        headers: ItemParams,
        request: Item,
        context: object,
    ) -> Item:
        return request

    handler.__annotations__[argument] = str

    with pytest.raises(
        RouteBindingError, match=rf"parameter '{argument}' annotation.*contract"
    ):
        route(declared, handler)


def test_route_rejects_unannotated_boundary_parameter() -> None:
    async def handler(request, context: object) -> Item:  # type: ignore[no-untyped-def]
        return request  # pyright: ignore[reportUnknownVariableType]

    with pytest.raises(
        RouteBindingError, match="parameter 'request' must be annotated"
    ):
        route(create_contract, handler)  # pyright: ignore[reportUnknownArgumentType]


def test_route_rejects_reserved_parameter_absent_from_contract() -> None:
    async def handler(context: object, request: Item | None = None) -> list[Item]:
        return [request] if request is not None else []

    with pytest.raises(RouteBindingError, match="contract declares no 'request'"):
        route(list_contract, handler)


def test_route_rejects_response_annotation_mismatch() -> None:
    async def handler(context: object) -> str:
        return "wrong"

    with pytest.raises(
        RouteBindingError, match=r"return annotation.*contract response"
    ):
        route(list_contract, handler)  # type: ignore[arg-type]


def test_route_requires_response_annotation() -> None:
    async def handler(context: object) -> list[Any]:
        return []

    del handler.__annotations__["return"]

    with pytest.raises(RouteBindingError, match="return value must be annotated"):
        route(list_contract, handler)


def test_route_resolves_boundary_annotations_without_resolving_context() -> None:
    source = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from nowhere import Ghost\n"
        "async def handler(request: Item, context: Ghost) -> Item:\n"
        "    return request\n"
    )
    namespace: dict[str, object] = {"Item": Item}
    exec(source, namespace)

    assert isinstance(
        route(create_contract, namespace["handler"]),  # type: ignore[arg-type]
        Route,
    )


def test_route_resolves_quoted_future_annotations() -> None:
    source = (
        "from __future__ import annotations\n"
        "async def handler(request: 'Item', context: object) -> 'Item':\n"
        "    return request\n"
    )
    namespace: dict[str, object] = {"Item": Item}
    exec(source, namespace)

    assert isinstance(
        route(create_contract, namespace["handler"]),  # type: ignore[arg-type]
        Route,
    )


def test_route_group_flattens_nested_groups() -> None:
    inner = route_group(route(list_contract, list_items))
    outer = route_group(inner, route(create_contract, create_item))

    assert [item.contract.name for item in outer] == [
        "GET /items",
        "POST /items",
    ]


def test_route_computes_headers_kwarg() -> None:
    headers_contract = contract(
        method="GET", path="/h", headers=ItemParams, response=Item
    )

    async def with_headers(headers: ItemParams, context: object) -> Item:
        return Item(name=headers.item_id)

    assert route(headers_contract, with_headers).call_kwargs == (
        "headers",
        "context",
    )


def test_route_group_errors_append_and_dedupe() -> None:
    unauthorized = ErrorDef(code="UNAUTHORIZED", status=401, message="Unauthorized")
    missing = ErrorDef(code="MISSING", status=404, message="Missing")
    declared = contract(
        method="GET", path="/one-item", response=Item, errors=(missing,)
    )

    async def handler(context: object) -> Item:
        return Item(name="x")

    group = route_group(
        route(declared, handler),
        errors=(unauthorized, missing),
    )

    assert group.routes[0].contract.errors == (missing, unauthorized)
    # The original contract is untouched.
    assert declared.errors == (missing,)


def test_route_group_rejects_error_code_conflicts_with_contract() -> None:
    original = ErrorDef(code="CONFLICT", status=409, message="Original")
    conflicting = ErrorDef(code="CONFLICT", status=409, message="Conflicting")
    declared = contract(
        method="GET", path="/one-item", response=Item, errors=(original,)
    )

    async def handler(context: object) -> Item:
        return Item(name="x")

    with pytest.raises(ConfigurationError, match=r"conflicting ErrorDef.*CONFLICT"):
        route_group(route(declared, handler), errors=(conflicting,))


def test_route_group_prefix_rewrites_paths() -> None:
    group = route_group(route(list_contract, list_items), prefix="/api")

    assert [item.contract.path for item in group] == ["/api/items"]
    assert [item.contract.name for item in group] == ["GET /api/items"]
    # The original contract is untouched.
    assert list_contract.path == "/items"
    assert list_contract.name == "GET /items"


def test_route_group_prefix_preserves_custom_contract_name() -> None:
    declared = contract(
        method="GET", path="/items", response=list[Item], name="items.list"
    )

    group = route_group(route(declared, list_items), prefix="/api")

    assert group.routes[0].contract.name == "items.list"


def test_route_group_rejects_relative_prefix() -> None:
    with pytest.raises(ValueError, match="prefix must start with '/'"):
        route_group(route(list_contract, list_items), prefix="api")


def test_route_group_rejects_malformed_items_and_errors() -> None:
    with pytest.raises(ConfigurationError, match=r"item\[0\].*Route"):
        route_group(["not-a-route"])  # type: ignore[list-item]

    with pytest.raises(ConfigurationError, match=r"errors\[0\].*ErrorDef"):
        route_group(errors=("UNAUTHORIZED",))  # type: ignore[arg-type]


def test_route_rejects_params_model_not_matching_path() -> None:
    class WrongParams(BaseModel):
        id: str

    declared = contract(
        method="GET", path="/items/{item_id}", params=WrongParams, response=Item
    )

    async def handler(params: WrongParams, context: object) -> Item:
        return Item(name="x")

    with pytest.raises(RouteBindingError, match="do not match path template"):
        route(declared, handler)


def test_route_rejects_params_types_without_declared_path_fields() -> None:
    declared = contract(
        method="GET",
        path="/items/{item_id}",
        params=dict[str, str],
        response=Item,
    )

    async def handler(params: dict[str, str], context: object) -> Item:
        return Item(name=params["item_id"])

    with pytest.raises(RouteBindingError, match="do not match path template"):
        route(declared, handler)


def test_route_rejects_placeholders_without_params_type() -> None:
    declared = contract(method="GET", path="/items/{item_id}", response=Item)

    async def handler(context: object) -> Item:
        return Item(name="x")

    with pytest.raises(RouteBindingError, match="no params type"):
        route(declared, handler)
