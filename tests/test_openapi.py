from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, Field, computed_field, create_model

from tenchi.contracts import contract
from tenchi.errors import ConfigurationError, ErrorDef
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


def test_openapi_rejects_bare_string_tag_sequences() -> None:
    with pytest.raises(ConfigurationError, match="tags must be a sequence"):
        openapi_route(make_group(), title="Items", version="1", tags="docs")


def test_openapi_rejects_malformed_document_metadata_and_security() -> None:
    with pytest.raises(ConfigurationError, match="title must be a non-empty string"):
        openapi_schema(make_group(), title=42, version="1")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="version must be a non-empty string"):
        openapi_schema(make_group(), title="Items", version="")
    with pytest.raises(ConfigurationError, match="description must be a string"):
        openapi_schema(
            make_group(),
            title="Items",
            version="1",
            description=42,  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(ConfigurationError, match="security must be a mapping"):
        openapi_schema(
            make_group(),
            title="Items",
            version="1",
            security="bearer",  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(ConfigurationError, match="scheme 'bearer' must be a mapping"):
        openapi_schema(
            make_group(),
            title="Items",
            version="1",
            security={  # pyright: ignore[reportArgumentType]
                "bearer": "http"
            },
        )


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


def test_success_response_headers_are_documented_with_wire_names() -> None:
    class ResultKind(StrEnum):
        CREATED = "created"
        RESTORED = "restored"

    class CreatedHeaders(BaseModel):
        location: str = Field(alias="Location")
        kind: ResultKind = Field(alias="X-Result-Kind")
        note: str | None = Field(default=None, alias="X-Note")

    declared = contract(
        method="POST",
        path="/created",
        request=Item,
        response=Item,
        response_headers=CreatedHeaders,
        status=201,
    )

    def response_headers(item: Item) -> CreatedHeaders:
        return CreatedHeaders(
            Location=f"/items/{item.name}",
            **{"X-Result-Kind": ResultKind.CREATED},
        )

    document = openapi_schema(
        route_group(route(declared, create_item, response_headers=response_headers)),
        title="X",
        version="1",
    )
    headers = document["paths"]["/created"]["post"]["responses"]["201"]["headers"]

    assert headers["Location"] == {
        "required": True,
        "schema": {"title": "Location", "type": "string"},
    }
    assert headers["X-Result-Kind"]["required"] is True
    assert headers["X-Result-Kind"]["schema"] == {
        "$ref": "#/components/schemas/ResultKind"
    }
    assert headers["X-Note"]["required"] is False


def test_response_header_fields_must_validate_their_wire_representation() -> None:
    class ComputedHeaders(BaseModel):
        source: str = Field(alias="X-Source")

        @computed_field(alias="X-Computed")
        @property
        def computed(self) -> str:
            return self.source.upper()

    declared = contract(
        method="GET",
        path="/computed-headers",
        response=Item,
        response_headers=ComputedHeaders,
    )

    def response_headers(item: Item) -> ComputedHeaders:
        return ComputedHeaders(**{"X-Source": item.name})

    async def handler(context: Context) -> Item:
        return Item(name="x")

    with pytest.raises(ConfigurationError, match="same field names"):
        openapi_schema(
            route_group(route(declared, handler, response_headers=response_headers)),
            title="X",
            version="1",
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


def test_starlette_path_converters_are_normalized_for_openapi() -> None:
    class NumericParams(BaseModel):
        item_id: int

    declared = contract(
        method="GET",
        path="/items/{item_id:int}",
        params=NumericParams,
        response=Item,
    )

    async def handler(params: NumericParams, context: Context) -> Item:
        return Item(name=str(params.item_id))

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="1"
    )

    assert "/items/{item_id}" in document["paths"]
    assert "/items/{item_id:int}" not in document["paths"]


def test_converter_routes_that_collide_in_openapi_are_rejected() -> None:
    class Params(BaseModel):
        item_id: str

    first = contract(
        method="GET", path="/items/{item_id:int}", params=Params, response=Item
    )
    second = contract(
        method="GET", path="/items/{item_id:str}", params=Params, response=Item
    )

    async def handler(params: Params, context: Context) -> Item:
        return Item(name=params.item_id)

    group = route_group(route(first, handler), route(second, handler))

    with pytest.raises(
        ConfigurationError, match=r"duplicate route GET /items/\{item_id\}"
    ):
        openapi_schema(group, title="X", version="1")


def test_equivalent_templates_with_different_parameter_names_are_rejected() -> None:
    class FirstParams(BaseModel):
        first: str

    class SecondParams(BaseModel):
        second: str

    first = contract(
        method="GET", path="/items/{first}", params=FirstParams, response=Item
    )
    second = contract(
        method="POST", path="/items/{second}", params=SecondParams, response=Item
    )

    async def get_item(params: FirstParams, context: Context) -> Item:
        return Item(name=params.first)

    async def replace_item(params: SecondParams, context: Context) -> Item:
        return Item(name=params.second)

    group = route_group(route(first, get_item), route(second, replace_item))

    with pytest.raises(ConfigurationError, match="conflicting route templates"):
        openapi_schema(group, title="X", version="1")


def test_explicit_head_and_get_operations_share_one_document_path() -> None:
    get_contract = contract(method="GET", path="/items", response=Item)
    head_contract = contract(method="HEAD", path="/items", response=None, status=204)

    async def get_items(context: Context) -> Item:
        return Item(name="x")

    async def head_items(context: Context) -> None:
        return None

    document = openapi_schema(
        route_group(
            route(get_contract, get_items),
            route(head_contract, head_items),
        ),
        title="X",
        version="1",
    )

    assert set(document["paths"]["/items"]) == {"get", "head"}


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


def test_pydantic_aliases_are_documented_as_wire_names() -> None:
    class AliasedParams(BaseModel):
        item_id: str = Field(alias="id")

    class AliasedItem(BaseModel):
        title: str = Field(alias="wireTitle")

    declared = contract(
        method="POST",
        path="/aliased/{id}",
        params=AliasedParams,
        request=AliasedItem,
        response=AliasedItem,
    )

    async def handler(
        params: AliasedParams, request: AliasedItem, context: Context
    ) -> AliasedItem:
        return request

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="1"
    )
    operation = document["paths"]["/aliased/{id}"]["post"]

    assert operation["parameters"][0]["name"] == "id"
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert set(request_schema["properties"]) == {"wireTitle"}
    assert set(response_schema["properties"]) == {"wireTitle"}


@pytest.mark.parametrize("slot", ["query", "headers"])
def test_scalar_mapping_input_slots_are_rejected(slot: str) -> None:
    declared = (
        contract(method="GET", path="/scalar", query=int)
        if slot == "query"
        else contract(method="GET", path="/scalar", headers=int)
    )

    async def query_handler(query: int, context: Context) -> None:
        return None

    async def headers_handler(headers: int, context: Context) -> None:
        return None

    handler = query_handler if slot == "query" else headers_handler
    group = route_group(route(declared, handler))

    with pytest.raises(ConfigurationError, match="object-shaped"):
        openapi_schema(group, title="X", version="1")


def test_recursive_object_input_root_reference_is_resolved() -> None:
    class RecursiveQuery(BaseModel):
        term: str = ""
        child: "RecursiveQuery | None" = None

    declared = contract(method="GET", path="/recursive", query=RecursiveQuery)

    async def handler(query: RecursiveQuery, context: Context) -> None:
        return None

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="1"
    )

    parameters = document["paths"]["/recursive"]["get"]["parameters"]
    assert [parameter["name"] for parameter in parameters] == ["term", "child"]


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


def test_declared_error_headers_are_deduplicated_case_insensitively() -> None:
    first = ErrorDef(
        code="FIRST_LIMIT",
        status=429,
        message="First",
        headers=("Retry-After",),
    )
    second = ErrorDef(
        code="SECOND_LIMIT",
        status=429,
        message="Second",
        headers=("retry-after",),
    )
    declared = contract(
        method="GET", path="/limited", response=Item, errors=(first, second)
    )

    async def handler(context: Context) -> Item:
        return Item(name="x")

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="1"
    )

    assert document["paths"]["/limited"]["get"]["responses"]["429"]["headers"] == {
        "Retry-After": {"schema": {"type": "string"}}
    }


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

    two.__annotations__["return"] = list[conflicting]

    group = route_group(
        route(contract(method="GET", path="/one", response=list[First]), one),
        route(contract(method="GET", path="/two", response=list[conflicting]), two),
    )

    with pytest.raises(ValueError, match="conflicting schemas for component 'First'"):
        openapi_schema(group, title="X", version="0")


def test_user_error_response_component_does_not_replace_framework_envelope() -> None:
    class ErrorResponse(BaseModel):
        value: int

    declared = contract(
        method="GET",
        path="/collision",
        response=list[ErrorResponse],
        errors=(item_missing,),
    )

    async def handler(context: Context) -> list[ErrorResponse]:
        return [ErrorResponse(value=1)]

    document = openapi_schema(
        route_group(route(declared, handler)), title="X", version="1"
    )
    components = document["components"]["schemas"]
    success_schema = document["paths"]["/collision"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    error_schema = document["paths"]["/collision"]["get"]["responses"]["404"][
        "content"
    ]["application/json"]["schema"]

    assert success_schema["items"] == {"$ref": "#/components/schemas/ErrorResponse"}
    assert set(components["ErrorResponse"]["properties"]) == {"value"}
    assert error_schema["$ref"] != "#/components/schemas/ErrorResponse"
    framework_component = error_schema["$ref"].rsplit("/", 1)[-1]
    assert set(components[framework_component]["properties"]) == {
        "code",
        "message",
        "details",
        "request_id",
    }


def test_framework_error_component_allocation_is_route_order_independent() -> None:
    class ErrorResponse(BaseModel):
        first: int

    class ErrorResponse_2(BaseModel):
        second: int

    first_contract = contract(
        method="GET",
        path="/first",
        response=list[ErrorResponse],
        errors=(item_missing,),
    )
    second_contract = contract(
        method="GET",
        path="/second",
        response=list[ErrorResponse_2],
    )

    async def first(context: Context) -> list[ErrorResponse]:
        return []

    async def second(context: Context) -> list[ErrorResponse_2]:
        return []

    document = openapi_schema(
        route_group(route(first_contract, first), route(second_contract, second)),
        title="X",
        version="1",
    )
    components = document["components"]["schemas"]
    error_schema = document["paths"]["/first"]["get"]["responses"]["404"]["content"][
        "application/json"
    ]["schema"]

    assert set(components["ErrorResponse"]["properties"]) == {"first"}
    assert set(components["ErrorResponse_2"]["properties"]) == {"second"}
    assert error_schema["$ref"] not in {
        "#/components/schemas/ErrorResponse",
        "#/components/schemas/ErrorResponse_2",
    }


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


def test_multiple_security_schemes_are_all_required() -> None:
    document = openapi_schema(
        make_group(),
        title="Items",
        version="1.2.3",
        security={
            "bearerAuth": {"type": "http", "scheme": "bearer"},
            "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-key"},
        },
    )

    assert document["security"] == [{"bearerAuth": [], "apiKeyAuth": []}]


def test_public_operations_are_exempt_from_security() -> None:
    open_contract = contract(
        method="GET",
        path="/health",
        response=Item,
        public=True,
    )
    closed_contract = contract(
        method="GET", path="/closed", response=Item, tags=("health",)
    )

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


def test_openapi_route_is_public_by_default_and_can_be_protected() -> None:
    group = make_group()

    assert openapi_route(group, title="Items", version="1").contract.public is True
    assert (
        openapi_route(group, title="Items", version="1", public=False).contract.public
        is False
    )


def test_request_bodies_document_framework_body_errors() -> None:
    document = make_document()

    assert "413" in document["paths"]["/items"]["post"]["responses"]
    assert "415" in document["paths"]["/items"]["post"]["responses"]
    # No request body: nothing to cap.
    assert "413" not in document["paths"]["/search"]["get"]["responses"]
    assert "415" not in document["paths"]["/search"]["get"]["responses"]


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
