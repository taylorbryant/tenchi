"""Named response variants and controlled response passthrough."""

from dataclasses import dataclass
from typing import assert_type

import httpx
import pytest
from pydantic import BaseModel, Field
from starlette.responses import Response, StreamingResponse

from tenchi.cli import route_map
from tenchi.client import Client, ClientResponse, UnexpectedResponseError
from tenchi.contracts import Contract, contract
from tenchi.errors import ERROR_SOURCE_HEADER, ConfigurationError
from tenchi.openapi import openapi_schema
from tenchi.responses import PresentedResponse, ResponseDef, present, response
from tenchi.routes import RouteBindingError, route, route_group
from tenchi.server import create_app
from tenchi.testing import open_client, open_http


class Item(BaseModel):
    name: str


class CreatedHeaders(BaseModel):
    location: str = Field(alias="Location")


class CachedHeaders(BaseModel):
    etag: str = Field(alias="ETag")


@dataclass(frozen=True, slots=True)
class SaveResult:
    item: Item
    created: bool


created = response(
    Item,
    status=201,
    headers=CreatedHeaders,
    description="A new item was created",
)
existing = response(
    Item,
    status=200,
    description="The existing item was returned",
)
save_contract: Contract[Item, CreatedHeaders | None] = contract(
    method="PUT",
    path="/items",
    request=Item,
    responses=(created, existing),
)


async def save_item(request: Item, context: object) -> SaveResult:
    return SaveResult(item=request, created=request.name != "existing")


def present_save(result: SaveResult) -> PresentedResponse:
    if result.created:
        return present(
            created,
            result.item,
            headers=CreatedHeaders(Location=f"/items/{result.item.name}"),
        )
    return present(existing, result.item)


streamed = response(
    bytes,
    status=200,
    media_type="application/octet-stream",
    passthrough=True,
)
stream_contract: Contract[bytes, None] = contract(
    method="GET",
    path="/export",
    responses=(streamed,),
)


async def export(context: object) -> tuple[bytes, bytes]:
    return b"first,", b"second"


def present_export(result: tuple[bytes, bytes]) -> PresentedResponse:
    return present(
        streamed,
        response=StreamingResponse(
            iter(result),
            status_code=200,
            media_type="application/octet-stream",
        ),
    )


def make_app():  # type: ignore[no-untyped-def]
    return create_app(
        routes=route_group(
            route(save_contract, save_item, present=present_save),
            route(stream_contract, export, present=present_export),
        ),
        context_factory=object,
    )


async def test_named_responses_drive_server_and_typed_client() -> None:
    assert_type(save_contract, Contract[Item, CreatedHeaders | None])
    assert save_contract.response is Item
    assert save_contract.response_headers == CreatedHeaders | None
    async with open_client(make_app()) as client:
        created_response = await client.call_with_response(
            save_contract, request=Item(name="new")
        )
        existing_response = await client.call_with_response(
            save_contract, request=Item(name="existing")
        )

    assert_type(
        created_response,
        ClientResponse[Item, CreatedHeaders | None],
    )
    assert created_response.http_response.status_code == 201
    assert created_response.definition is created
    assert created_response.headers == CreatedHeaders(Location="/items/new")
    assert existing_response.http_response.status_code == 200
    assert existing_response.definition is existing
    assert existing_response.headers is None


def test_route_map_reports_response_definition_statuses() -> None:
    [entry] = route_map(
        route_group(route(save_contract, save_item, present=present_save))
    )

    assert entry["status"] is None
    assert entry["responses"] == [{"status": 201}, {"status": 200}]


async def test_passthrough_preserves_streaming_response_and_client_contract() -> None:
    async with open_client(make_app()) as client:
        response = await client.call_with_response(stream_contract)

    assert response.body == b"first,second"
    assert response.definition is streamed
    assert response.http_response.headers["content-type"].startswith(
        "application/octet-stream"
    )
    assert "x-request-id" in response.http_response.headers


async def test_invalid_passthrough_metadata_rolls_back_to_framework_500() -> None:
    async def value(context: object) -> str:
        return "wrong"

    def wrong_status(result: str) -> PresentedResponse:
        return present(
            streamed,
            response=StreamingResponse(
                iter((result.encode(),)),
                status_code=206,
                media_type="application/octet-stream",
            ),
        )

    app = create_app(
        routes=route_group(route(stream_contract, value, present=wrong_status)),
        context_factory=object,
    )
    async with open_http(app) as http:
        response = await http.get("/export")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"


async def test_passthrough_rejects_undeclared_application_headers() -> None:
    async def value(context: object) -> bytes:
        return b"data"

    def extra_header(result: bytes) -> PresentedResponse:
        return present(
            streamed,
            response=StreamingResponse(
                iter((result,)),
                media_type="application/octet-stream",
                headers={"X-Undeclared": "value"},
            ),
        )

    app = create_app(
        routes=route_group(route(stream_contract, value, present=extra_header)),
        context_factory=object,
    )
    async with open_http(app) as http:
        response = await http.get("/export")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"


async def test_passthrough_rejects_unsafe_values_even_for_declared_headers() -> None:
    declared_response = response(
        str,
        status=200,
        headers=CreatedHeaders,
        media_type="text/plain",
        passthrough=True,
    )
    declared: Contract[str, CreatedHeaders] = contract(
        method="GET",
        path="/unsafe-header",
        responses=(declared_response,),
    )

    async def value(context: object) -> str:
        return "ok"

    def inject(result: str) -> PresentedResponse:
        return present(
            declared_response,
            response=Response(
                result,
                media_type="text/plain",
                headers={"Location": "/ok\r\nX-Evil: yes"},
            ),
        )

    app = create_app(
        routes=route_group(route(declared, value, present=inject)),
        context_factory=object,
    )
    async with open_http(app) as http:
        http_response = await http.get("/unsafe-header")

    assert http_response.status_code == 500
    assert http_response.headers[ERROR_SOURCE_HEADER] == "framework"


async def test_passthrough_enforces_declared_media_type_parameters() -> None:
    declared_response = response(
        str,
        status=200,
        media_type="text/plain; charset=utf-8",
        passthrough=True,
    )
    declared: Contract[str, None] = contract(
        method="GET",
        path="/utf8-text",
        responses=(declared_response,),
    )

    async def value(context: object) -> str:
        return "ok"

    def wrong_charset(result: str) -> PresentedResponse:
        return present(
            declared_response,
            response=Response(
                result.encode("latin-1"),
                headers={"content-type": "text/plain; charset=iso-8859-1"},
            ),
        )

    app = create_app(
        routes=route_group(route(declared, value, present=wrong_charset)),
        context_factory=object,
    )
    async with open_http(app) as http:
        http_response = await http.get("/utf8-text")

    assert http_response.status_code == 500
    assert http_response.headers[ERROR_SOURCE_HEADER] == "framework"


async def test_no_body_passthrough_rejects_streaming_responses() -> None:
    no_content = response(
        None,
        status=204,
        media_type=None,
        passthrough=True,
    )
    declared: Contract[None, None] = contract(
        method="DELETE",
        path="/item",
        responses=(no_content,),
    )

    async def remove(context: object) -> None:
        return None

    def stream_body(result: None) -> PresentedResponse:
        return present(
            no_content,
            response=StreamingResponse(iter((b"not empty",)), status_code=204),
        )

    app = create_app(
        routes=route_group(route(declared, remove, present=stream_body)),
        context_factory=object,
    )
    async with open_http(app) as http:
        http_response = await http.delete("/item")

    assert http_response.status_code == 500
    assert http_response.headers[ERROR_SOURCE_HEADER] == "framework"


async def test_no_body_passthrough_accepts_a_concrete_empty_response() -> None:
    no_content = response(
        None,
        status=204,
        passthrough=True,
    )
    declared: Contract[None, None] = contract(
        method="DELETE",
        path="/empty-item",
        responses=(no_content,),
    )

    async def remove(context: object) -> None:
        return None

    def empty_response(result: None) -> PresentedResponse:
        return present(no_content, response=Response(status_code=204))

    app = create_app(
        routes=route_group(route(declared, remove, present=empty_response)),
        context_factory=object,
    )
    async with open_http(app) as http:
        http_response = await http.delete("/empty-item")

    assert http_response.status_code == 204
    assert http_response.content == b""
    assert "x-request-id" in http_response.headers


async def test_client_rejects_a_body_for_a_no_body_response() -> None:
    no_content = response(
        None,
        status=204,
        media_type=None,
    )
    declared: Contract[None, None] = contract(
        method="DELETE",
        path="/item",
        responses=(no_content,),
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204, content=b"not empty")

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(
            UnexpectedResponseError, match="requires an empty body"
        ) as excinfo:
            await client.call(declared)

    assert excinfo.value.reason == "the declared response requires an empty body"


def test_openapi_documents_each_response_definition() -> None:
    document = openapi_schema(
        route_group(route(save_contract, save_item, present=present_save)),
        title="Test",
        version="1",
    )
    responses = document["paths"]["/items"]["put"]["responses"]

    assert responses["201"]["description"] == "A new item was created"
    assert "Location" in responses["201"]["headers"]
    assert responses["200"]["description"] == "The existing item was returned"


def test_response_declarations_fail_early_when_ambiguous_or_incoherent() -> None:
    duplicate_status = response(Item, status=200)
    with pytest.raises(ConfigurationError, match="status 200 more than once"):
        contract(
            method="GET",
            path="/duplicate",
            responses=(existing, duplicate_status),
        )

    wrong = response(str, status=202)
    with pytest.raises(ConfigurationError, match="do not also pass response"):
        contract(  # pyright: ignore[reportCallIssue]
            method="GET",
            path="/wrong",
            response=Item,  # pyright: ignore[reportArgumentType]
            responses=(wrong,),
        )

    with pytest.raises(ConfigurationError, match="status= is only valid"):
        contract(
            method="GET",
            path="/redundant-status",
            status=202,
            responses=(existing,),
        )

    with pytest.raises(ConfigurationError, match="cannot declare a response body"):
        response(Item, status=204)

    with pytest.raises(ConfigurationError, match="unsupported charset"):
        response(
            str,
            status=200,
            media_type="text/plain; charset=not-a-codec",
        )

    with pytest.raises(
        ConfigurationError,
        match="pass top-level union members as separate positional body alternatives",
    ):
        response(Item | str, status=200)  # pyright: ignore[reportCallIssue]

    with pytest.raises(
        ConfigurationError,
        match="headers must be one object-shaped schema",
    ):
        response(  # pyright: ignore[reportCallIssue]
            Item,
            status=200,
            headers=CreatedHeaders | CachedHeaders,
        )


def test_a_response_may_itself_declare_a_union_within_the_exact_aggregate() -> None:
    flexible = response(Item, str, status=200)
    counted = response(int, status=206)

    declared = contract(
        method="GET",
        path="/flexible",
        responses=(flexible, counted),
    )

    assert_type(flexible, ResponseDef[Item | str, None])
    assert_type(declared, Contract[Item | str | int, None])
    assert flexible.body == Item | str
    assert declared.response == Item | str | int
    assert declared.responses == (flexible, counted)


def test_nested_and_nullable_body_unions_preserve_inference() -> None:
    nested = response(list[Item | str], status=200)
    nullable = response(Item, type(None), status=200)

    assert_type(nested, ResponseDef[list[Item | str], None])
    assert_type(nullable, ResponseDef[Item | None, None])
    assert nested.body == list[Item | str]
    assert nullable.body == Item | None


async def test_body_alternatives_flow_through_the_typed_client() -> None:
    flexible = response(Item, str, status=200)
    declared = contract(
        method="GET",
        path="/flexible",
        responses=(flexible,),
    )

    async def read_flexible(context: object) -> Item | str:
        return "ready"

    def present_flexible(result: Item | str) -> PresentedResponse:
        return present(flexible, result)

    app = create_app(
        routes=route_group(route(declared, read_flexible, present=present_flexible)),
        context_factory=object,
    )
    async with open_client(app) as client:
        result = await client.call(declared)

    assert_type(result, Item | str)
    assert result == "ready"


def test_route_requires_a_typed_synchronous_presenter() -> None:
    with pytest.raises(RouteBindingError, match="pass a typed present"):
        route(save_contract, save_item)  # type: ignore[arg-type]

    async def async_presenter(result: SaveResult) -> PresentedResponse:
        return present_save(result)

    with pytest.raises(RouteBindingError, match="synchronous function"):
        route(  # pyright: ignore[reportCallIssue]
            save_contract,
            save_item,
            present=async_presenter,  # type: ignore[arg-type]
        )


def test_present_rejects_wrong_channels_at_construction() -> None:
    with pytest.raises(ConfigurationError, match="requires a body"):
        present(  # pyright: ignore[reportCallIssue, reportArgumentType]
            existing  # pyright: ignore[reportArgumentType]
        )


def test_present_accepts_typed_headers_for_a_no_body_response() -> None:
    no_content = response(
        None,
        status=204,
        headers=CreatedHeaders,
        media_type=None,
    )

    presented = present(
        no_content,
        headers=CreatedHeaders(Location="/items/removed"),
    )

    assert presented.headers == CreatedHeaders(Location="/items/removed")
    with pytest.raises(ConfigurationError, match="only accepted for a passthrough"):
        present(  # pyright: ignore[reportCallIssue]
            existing,  # pyright: ignore[reportArgumentType]
            Item(name="x"),
            response=StreamingResponse(  # pyright: ignore[reportArgumentType]
                iter(())
            ),
        )
