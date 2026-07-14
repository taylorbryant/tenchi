"""Typed client behavior against an inline ASGI app."""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from pydantic import BaseModel
from starlette.applications import Starlette

from tenchi.client import Client, UnexpectedResponseError
from tenchi.contracts import contract
from tenchi.errors import AppError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import create_app


class Item(BaseModel):
    name: str


class ItemParams(BaseModel):
    item_id: str


class SearchQuery(BaseModel):
    term: str = ""
    limit: int = 10


@dataclass(frozen=True, slots=True)
class Context:
    pass


item_missing = ErrorDef(code="ITEM_MISSING", status=404, message="Item missing")

create_item_contract = contract(
    method="POST", path="/items", request=Item, response=Item, status=201
)
get_item_contract = contract(
    method="GET",
    path="/items/{item_id}",
    params=ItemParams,
    response=Item,
    errors=(item_missing,),
)
search_contract = contract(
    method="GET", path="/search", query=SearchQuery, response=SearchQuery
)
clear_contract = contract(method="DELETE", path="/items", status=204)
shout_contract = contract(
    method="POST",
    path="/shout",
    request=str,
    request_media_type="text/plain",
    response=str,
    response_media_type="text/plain",
)
checksum_contract = contract(
    method="POST",
    path="/checksum",
    request=bytes,
    request_media_type="application/octet-stream",
    response=bytes,
    response_media_type="application/octet-stream",
)


class ClientHeaders(BaseModel):
    x_api_key: str
    accept_language: str = "en"


headers_contract = contract(
    method="GET", path="/headers", headers=ClientHeaders, response=str
)


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    async def create_item(request: Item, context: Context) -> Item:
        return request

    async def get_item(params: ItemParams, context: Context) -> Item:
        if params.item_id == "missing":
            raise AppError(item_missing, details={"item_id": params.item_id})
        if params.item_id == "explode":
            raise RuntimeError("explode")
        return Item(name=params.item_id)

    async def search(query: SearchQuery, context: Context) -> SearchQuery:
        return query

    async def clear(context: Context) -> None:
        return None

    async def shout(request: str, context: Context) -> str:
        return request.upper()

    async def checksum(request: bytes, context: Context) -> bytes:
        return bytes([sum(request) % 256])

    async def read_headers(headers: ClientHeaders, context: Context) -> str:
        return f"{headers.x_api_key}/{headers.accept_language}"

    app = create_app(
        routes=route_group(
            route(create_item_contract, create_item),
            route(get_item_contract, get_item),
            route(search_contract, search),
            route(clear_contract, clear),
            route(shout_contract, shout),
            route(checksum_contract, checksum),
            route(headers_contract, read_headers),
        ),
        context_factory=Context,
    )
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    async with Client(http=http) as tenchi_client:
        yield tenchi_client
    await http.aclose()


async def test_call_returns_validated_response(client: Client) -> None:
    item = await client.call(create_item_contract, request=Item(name="milk"))

    assert isinstance(item, Item)
    assert item.name == "milk"


async def test_call_substitutes_path_params(client: Client) -> None:
    item = await client.call(get_item_contract, params=ItemParams(item_id="abc"))

    assert item == Item(name="abc")


async def test_call_sends_query_params(client: Client) -> None:
    result = await client.call(search_contract, query=SearchQuery(term="milk", limit=3))

    assert result == SearchQuery(term="milk", limit=3)


async def test_call_omits_query_to_use_defaults(client: Client) -> None:
    result = await client.call(search_contract)

    assert result == SearchQuery()


async def test_call_returns_none_for_empty_response(client: Client) -> None:
    assert await client.call(clear_contract) is None


async def test_call_sends_headers_with_hyphenated_names(client: Client) -> None:
    result = await client.call(
        headers_contract,
        headers=ClientHeaders(x_api_key="abc", accept_language="fr"),
    )

    assert result == "abc/fr"


async def test_call_round_trips_text_media_type(client: Client) -> None:
    assert await client.call(shout_contract, request="hello") == "HELLO"


async def test_call_round_trips_binary_media_type(client: Client) -> None:
    assert await client.call(checksum_contract, request=b"\x01\x02\x03") == b"\x06"


async def test_declared_error_raises_app_error(client: Client) -> None:
    with pytest.raises(AppError) as excinfo:
        await client.call(get_item_contract, params=ItemParams(item_id="missing"))

    assert excinfo.value.definition == item_missing
    assert excinfo.value.details == {"item_id": "missing"}


async def test_undeclared_error_raises_unexpected_response(
    client: Client,
) -> None:
    with pytest.raises(UnexpectedResponseError) as excinfo:
        await client.call(get_item_contract, params=ItemParams(item_id="explode"))

    assert excinfo.value.status_code == 500
    assert excinfo.value.body["code"] == "INTERNAL_SERVER_ERROR"


async def test_call_requires_declared_params_and_request(
    client: Client,
) -> None:
    with pytest.raises(TypeError, match="pass params="):
        await client.call(get_item_contract)

    with pytest.raises(TypeError, match="pass request="):
        await client.call(create_item_contract)


async def test_client_level_errors_cover_undeclared_hook_errors() -> None:
    """Client(errors=...) types errors the contract itself never declared."""
    throttled = ErrorDef(code="THROTTLED", status=429, message="Slow down")
    plain_contract = contract(method="GET", path="/plain", response=Item)

    async def always_throttled(context: Context) -> Item:
        raise AppError(throttled)

    app = create_app(
        routes=route_group(
            route(plain_contract, always_throttled), errors=(throttled,)
        ),
        context_factory=Context,
    )
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )
    async with Client(http=http, errors=(throttled,)) as tenchi_client:
        with pytest.raises(AppError) as excinfo:
            await tenchi_client.call(plain_contract)
    await http.aclose()

    assert excinfo.value.definition == throttled


def make_app() -> Starlette:
    async def read_headers(headers: ClientHeaders, context: Context) -> str:
        return f"{headers.x_api_key}/{headers.accept_language}"

    return create_app(
        routes=route_group(route(headers_contract, read_headers)),
        context_factory=Context,
    )


async def test_owned_client_with_transport_and_default_headers() -> None:
    app = make_app()

    async with Client(
        transport=httpx.ASGITransport(app=app),
        headers={"x-api-key": "from-default"},
    ) as client:
        result = await client.call(headers_contract)

    assert result == "from-default/en"


async def test_per_call_headers_override_client_defaults() -> None:
    app = make_app()

    async with Client(
        transport=httpx.ASGITransport(app=app),
        headers={"x-api-key": "from-default", "accept-language": "de"},
    ) as client:
        result = await client.call(
            headers_contract, headers=ClientHeaders(x_api_key="per-call")
        )

    # The per-call model wins for every field it defines — including its
    # defaulted accept_language — because the model fully describes the
    # contract's header inputs.
    assert result == "per-call/en"


def test_client_constructor_validation() -> None:
    with pytest.raises(ValueError, match="requires base_url="):
        Client()

    with pytest.raises(ValueError, match="mutually exclusive"):
        Client(base_url="http://x", http=httpx.AsyncClient())

    with pytest.raises(ValueError, match="mutually exclusive"):
        Client(headers={"a": "b"}, http=httpx.AsyncClient())
