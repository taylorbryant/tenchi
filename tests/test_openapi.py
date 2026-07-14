from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, create_model

from tenchi.contracts import contract
from tenchi.errors import ErrorDef
from tenchi.openapi import openapi_route, openapi_schema
from tenchi.routes import route, route_group
from tenchi.server import create_app


class Item(BaseModel):
    name: str


class ItemParams(BaseModel):
    item_id: str


class SearchQuery(BaseModel):
    term: str
    limit: int = 10


@dataclass(frozen=True, slots=True)
class Context:
    pass


item_missing = ErrorDef(code="ITEM_MISSING", status=404, message="Item missing")
item_gone = ErrorDef(code="ITEM_GONE", status=404, message="Item gone")


async def create_item(request: Item, context: Context) -> Item:
    return request


async def get_item(params: ItemParams, context: Context) -> Item:
    return Item(name=params.item_id)


async def search_items(query: SearchQuery, context: Context) -> list[Item]:
    return []


async def clear_items(context: Context) -> None:
    return None


def make_group():
    return route_group(
        route(
            contract(
                method="POST", path="/items", request=Item, response=Item, status=201
            ),
            create_item,
        ),
        route(
            contract(
                method="GET",
                path="/items/{item_id}",
                params=ItemParams,
                response=Item,
                errors=(item_missing, item_gone),
            ),
            get_item,
        ),
        route(
            contract(
                method="GET", path="/search", query=SearchQuery, response=list[Item]
            ),
            search_items,
        ),
        route(contract(method="DELETE", path="/items", status=204), clear_items),
    )


def make_document():
    return openapi_schema(make_group(), title="Items", version="1.2.3")


def test_document_skeleton() -> None:
    document = make_document()

    assert document["openapi"] == "3.1.0"
    assert document["info"] == {"title": "Items", "version": "1.2.3"}
    assert set(document["paths"]) == {"/items", "/items/{item_id}", "/search"}
    assert set(document["paths"]["/items"]) == {"post", "delete"}


def test_request_body_and_success_response() -> None:
    operation = make_document()["paths"]["/items"]["post"]

    assert operation["operationId"] == "create_item"
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["properties"]["name"] == {"title": "Name", "type": "string"}
    assert "201" in operation["responses"]
    assert (
        operation["responses"]["201"]["content"]["application/json"]["schema"]["type"]
        == "object"
    )


def test_path_parameters_are_required() -> None:
    operation = make_document()["paths"]["/items/{item_id}"]["get"]

    assert operation["parameters"] == [
        {
            "name": "item_id",
            "in": "path",
            "required": True,
            "schema": {"title": "Item Id", "type": "string"},
        }
    ]


def test_query_parameters_reflect_field_requiredness() -> None:
    parameters = {
        parameter["name"]: parameter
        for parameter in make_document()["paths"]["/search"]["get"]["parameters"]
    }

    assert parameters["term"]["in"] == "query"
    assert parameters["term"]["required"] is True
    assert parameters["limit"]["required"] is False


def test_list_response_references_component() -> None:
    document = make_document()
    schema = document["paths"]["/search"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]

    assert schema == {
        "items": {"$ref": "#/components/schemas/Item"},
        "type": "array",
    }
    assert "Item" in document["components"]["schemas"]


def test_declared_errors_become_responses() -> None:
    responses = make_document()["paths"]["/items/{item_id}"]["get"]["responses"]

    assert responses["404"]["description"] == (
        "ITEM_MISSING: Item missing; ITEM_GONE: Item gone"
    )
    assert responses["404"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_validation_error_response_tracks_validated_inputs() -> None:
    document = make_document()

    assert "422" in document["paths"]["/items"]["post"]["responses"]
    assert "422" in document["paths"]["/search"]["get"]["responses"]
    # No request, params, or query: nothing for the framework to reject.
    assert "422" not in document["paths"]["/items"]["delete"]["responses"]


def test_empty_response_has_no_content() -> None:
    response = make_document()["paths"]["/items"]["delete"]["responses"]["204"]

    assert response == {"description": "Successful response"}


def test_duplicate_use_case_names_get_unique_operation_ids() -> None:
    first = contract(method="GET", path="/a", response=Item)
    second = contract(method="GET", path="/b", response=Item)

    async def handler(context: Context) -> Item:
        return Item(name="x")

    group = route_group(route(first, handler), route(second, handler))
    document = openapi_schema(group, title="X", version="0")

    assert document["paths"]["/a"]["get"]["operationId"] == "handler"
    assert document["paths"]["/b"]["get"]["operationId"] == "handler_2"


def test_header_parameters_use_hyphenated_names() -> None:
    class AuthHeaders(BaseModel):
        x_api_key: str

    declared = contract(
        method="GET", path="/secure", headers=AuthHeaders, response=Item
    )

    async def handler(headers: AuthHeaders, context: Context) -> Item:
        return Item(name="x")

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )
    operation = document["paths"]["/secure"]["get"]

    assert operation["parameters"] == [
        {
            "name": "x-api-key",
            "in": "header",
            "required": True,
            "schema": {"title": "X Api Key", "type": "string"},
        }
    ]
    assert "422" in operation["responses"]


def test_group_level_errors_are_documented_on_every_route() -> None:
    unauthorized = ErrorDef(code="UNAUTHORIZED", status=401, message="Unauthorized")
    group = route_group(make_group(), errors=(unauthorized,))

    document = openapi_schema(group, title="X", version="0")

    for path, operations in document["paths"].items():
        for operation in operations.values():
            assert "401" in operation["responses"], path


def test_operation_metadata_flows_from_the_contract() -> None:
    declared = contract(
        method="GET",
        path="/meta",
        response=Item,
        summary="Get the item",
        description="Longer prose.",
        tags=("items", "admin"),
        deprecated=True,
    )

    async def handler(context: Context) -> Item:
        """Docstring that should NOT be used."""
        return Item(name="x")

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )
    operation = document["paths"]["/meta"]["get"]

    assert operation["summary"] == "Get the item"
    assert operation["description"] == "Longer prose."
    assert operation["tags"] == ["items", "admin"]
    assert operation["deprecated"] is True


def test_description_falls_back_to_use_case_docstring() -> None:
    declared = contract(method="GET", path="/doc", response=Item)

    async def handler(context: Context) -> Item:
        """Fetch the item from the repository."""
        return Item(name="x")

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )

    assert (
        document["paths"]["/doc"]["get"]["description"]
        == "Fetch the item from the repository."
    )


def test_media_types_flow_into_content_keys() -> None:
    declared = contract(
        method="POST",
        path="/raw",
        request=bytes,
        request_media_type="application/octet-stream",
        response=str,
        response_media_type="text/plain",
    )

    async def handler(request: bytes, context: Context) -> str:
        return "ok"

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )
    operation = document["paths"]["/raw"]["post"]

    body_content = operation["requestBody"]["content"]
    assert list(body_content) == ["application/octet-stream"]
    assert body_content["application/octet-stream"]["schema"] == {
        "format": "binary",
        "type": "string",
    }
    response_content = operation["responses"]["200"]["content"]
    assert list(response_content) == ["text/plain"]
    assert response_content["text/plain"]["schema"] == {"type": "string"}


def test_declared_error_headers_are_documented() -> None:
    throttled = ErrorDef(
        code="THROTTLED",
        status=429,
        message="Slow down",
        headers=("Retry-After",),
    )
    declared = contract(
        method="GET", path="/limited", response=Item, errors=(throttled,)
    )

    async def handler(context: Context) -> Item:
        return Item(name="x")

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )
    response = document["paths"]["/limited"]["get"]["responses"]["429"]

    assert response["headers"] == {"Retry-After": {"schema": {"type": "string"}}}


def test_conflicting_component_names_are_rejected() -> None:
    class First(BaseModel):
        a: int

    # A second, different model with the same class name — as happens
    # when two features each define e.g. an ``Item``.
    conflicting = create_model("First", b=(str, ...))

    async def one(context: Context) -> list[First]:
        return [First(a=1)]

    async def two(context: Context) -> Any:
        raise NotImplementedError

    group = route_group(
        route(contract(method="GET", path="/one", response=list[First]), one),
        route(contract(method="GET", path="/two", response=list[conflicting]), two),
    )

    with pytest.raises(ValueError, match="conflicting schemas for component 'First'"):
        openapi_schema(group, title="X", version="0")


def test_declared_422_merges_with_framework_validation_error() -> None:
    unprocessable = ErrorDef(code="UNPROCESSABLE", status=422, message="Nope")
    declared = contract(
        method="POST",
        path="/strict",
        request=Item,
        response=Item,
        errors=(unprocessable,),
    )

    async def handler(request: Item, context: Context) -> Item:
        return request

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )
    description = document["paths"]["/strict"]["post"]["responses"]["422"][
        "description"
    ]

    assert "UNPROCESSABLE" in description
    assert "VALIDATION_ERROR" in description


def test_duplicate_routes_are_rejected() -> None:
    declared = contract(method="GET", path="/dup", response=Item)

    async def handler(context: Context) -> Item:
        return Item(name="x")

    group = route_group(route(declared, handler), route(declared, handler))

    with pytest.raises(ValueError, match="duplicate route GET /dup"):
        openapi_schema(group, title="X", version="0")


def test_security_schemes_apply_globally() -> None:
    document = openapi_schema(
        make_group(),
        title="Items",
        version="1.2.3",
        security={"bearerAuth": {"type": "http", "scheme": "bearer"}},
    )

    assert document["security"] == [{"bearerAuth": []}]
    assert document["components"]["securitySchemes"] == {
        "bearerAuth": {"type": "http", "scheme": "bearer"}
    }
    for operations in document["paths"].values():
        for operation in operations.values():
            assert "security" not in operation


def test_public_tagged_operations_are_exempt_from_security() -> None:
    open_contract = contract(
        method="GET", path="/health", response=Item, tags=("health",)
    )
    closed_contract = contract(method="GET", path="/closed", response=Item)

    async def handler(context: Context) -> Item:
        return Item(name="x")

    document = openapi_schema(
        route_group(route(open_contract, handler), route(closed_contract, handler)),
        title="X",
        version="0",
        security={"apiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-key"}},
    )

    assert document["paths"]["/health"]["get"]["security"] == []
    assert "security" not in document["paths"]["/closed"]["get"]


def test_documents_without_security_are_unchanged() -> None:
    document = make_document()

    assert "security" not in document
    assert "securitySchemes" not in document.get("components", {})


async def test_openapi_route_serves_document_without_documenting_itself() -> None:
    group = make_group()
    app = create_app(
        routes=route_group(group, openapi_route(group, title="Items", version="1.2.3")),
        context_factory=Context,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert document["info"] == {"title": "Items", "version": "1.2.3"}
    assert "/openapi.json" not in document["paths"]


def test_request_bodies_document_the_413() -> None:
    document = make_document()

    assert "413" in document["paths"]["/items"]["post"]["responses"]
    # No request body: nothing to cap.
    assert "413" not in document["paths"]["/search"]["get"]["responses"]


def test_sunset_becomes_a_vendor_extension() -> None:
    from datetime import UTC, datetime

    declared = contract(
        method="GET",
        path="/old",
        response=Item,
        deprecated=True,
        sunset=datetime(2027, 1, 1, tzinfo=UTC),
    )

    async def handler(context: Context) -> Item:
        return Item(name="x")

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )
    operation = document["paths"]["/old"]["get"]

    assert operation["deprecated"] is True
    assert operation["x-sunset"] == "2027-01-01T00:00:00+00:00"


def test_document_with_lifecycle_metadata_is_valid_openapi() -> None:
    from datetime import UTC, datetime

    from openapi_spec_validator import validate

    declared = contract(
        method="POST",
        path="/old",
        request=Item,
        response=Item,
        deprecated=datetime(2026, 6, 1, tzinfo=UTC),
        sunset=datetime(2027, 1, 1, tzinfo=UTC),
        max_request_bytes=1024,
    )

    async def handler(request: Item, context: Context) -> Item:
        return request

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="0"
    )

    validate(document)  # x-sunset and the 413 must not break OAS 3.1
    assert document["paths"]["/old"]["post"]["x-sunset"] == "2027-01-01T00:00:00+00:00"
