"""Typed client behavior against an inline ASGI app."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, assert_type

import httpx
import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    SecretStr,
    ValidationError,
    computed_field,
    model_serializer,
)
from starlette.applications import Starlette

from tenchi.client import Client, ClientResponse, UnexpectedResponseError
from tenchi.contracts import contract
from tenchi.errors import (
    ERROR_SOURCE_HEADER,
    AppError,
    ConfigurationError,
    ErrorDef,
    validation_error,
)
from tenchi.routes import route, route_group
from tenchi.server import create_app


class Item(BaseModel):
    name: str


class ItemParams(BaseModel):
    item_id: str


class SearchQuery(BaseModel):
    term: str = ""
    limit: int = 10


class CreatedHeaders(BaseModel):
    location: str = Field(alias="Location")
    revision: int = Field(alias="X-Revision")


@dataclass(frozen=True, slots=True)
class Context:
    pass


item_missing = ErrorDef(code="ITEM_MISSING", status=404, message="Item missing")

create_item_contract = contract(
    method="POST",
    path="/items",
    request=Item,
    response=Item,
    response_headers=CreatedHeaders,
    status=201,
)
get_item_contract = contract(
    method="GET",
    path="/items/{item_id}",
    params=ItemParams,
    response=Item,
    errors=(item_missing,),
)


async def test_client_rechecks_idempotency_headers_before_io() -> None:
    class EmptyKeyAllowed(BaseModel):
        idempotency_key: str

    declared = contract(
        method="POST",
        path="/commands",
        headers=EmptyKeyAllowed,
        idempotency_key=True,
    )
    called = False

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(
            ConfigurationError,
            match="required, non-empty string field named 'Idempotency-Key'",
        ):
            await client.call(
                declared,
                headers=EmptyKeyAllowed(idempotency_key="key"),
            )

    assert called is False


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

    def create_headers(item: Item) -> CreatedHeaders:
        return CreatedHeaders.model_validate(
            {"Location": f"/items/{item.name}", "X-Revision": 2}
        )

    app = create_app(
        routes=route_group(
            route(
                create_item_contract,
                create_item,
                response_headers=create_headers,
            ),
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


async def test_call_with_response_returns_typed_headers_and_http_response(
    client: Client,
) -> None:
    result = await client.call_with_response(
        create_item_contract, request=Item(name="milk")
    )

    assert_type(result, ClientResponse[Item, CreatedHeaders])
    assert result.body == Item(name="milk")
    assert result.headers == CreatedHeaders.model_validate(
        {"Location": "/items/milk", "X-Revision": 2}
    )
    assert result.http_response.status_code == 201


async def test_call_with_response_types_undeclared_headers_as_none(
    client: Client,
) -> None:
    result = await client.call_with_response(
        get_item_contract, params=ItemParams(item_id="abc")
    )

    assert_type(result, ClientResponse[Item, None])
    assert result.headers is None


async def test_client_rejects_missing_required_success_header() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"name": "milk"})

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValidationError):
            await client.call(
                create_item_contract,
                request=Item(name="milk"),
            )


async def test_client_rejects_non_round_trip_response_header_fields_before_io() -> None:
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
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"name": "x"})

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match="same field names"):
            await client.call(declared)

    assert requests == []


async def test_call_distinguishes_json_null_request_from_omitted_request() -> None:
    declared = contract(
        method="POST",
        path="/nullable",
        request=Item | None,
        response=Item | None,
    )

    async def echo(request: Item | None, context: Context) -> Item | None:
        return request

    app = create_app(routes=route_group(route(declared, echo)), context_factory=Context)

    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        result = await tenchi_client.call(declared, request=None)

    assert result is None


async def test_call_substitutes_path_params(client: Client) -> None:
    item = await client.call(get_item_contract, params=ItemParams(item_id="abc"))

    assert item == Item(name="abc")


async def test_call_rejects_lossy_path_parameter_serializers_before_io() -> None:
    def increment(value: int) -> str:
        return str(value + 1)

    class SecretParams(BaseModel):
        value: SecretStr

    class ChangedParams(BaseModel):
        value: Annotated[int, PlainSerializer(increment, return_type=str)]

    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    cases = (
        (SecretParams, SecretParams(value=SecretStr("secret"))),
        (ChangedParams, ChangedParams(value=1)),
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        for params_type, value in cases:
            declared = contract(
                method="GET",
                path="/items/{value}",
                params=params_type,
                status=204,
            )
            with pytest.raises(ConfigurationError, match="must round-trip"):
                await client.call(declared, params=value)

    assert requests == []


async def test_call_substitutes_starlette_path_converters() -> None:
    class NumericParams(BaseModel):
        item_id: int

    declared = contract(
        method="GET",
        path="/items/{item_id:int}",
        params=NumericParams,
        status=204,
    )
    paths: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        await client.call(declared, params=NumericParams(item_id=7))

    assert paths == ["/items/7"]


async def test_call_sends_query_params(client: Client) -> None:
    result = await client.call(search_contract, query=SearchQuery(term="milk", limit=3))

    assert result == SearchQuery(term="milk", limit=3)


async def test_call_omits_query_to_use_defaults(client: Client) -> None:
    result = await client.call(search_contract)

    assert result == SearchQuery()


async def test_omitted_query_can_use_inherited_httpx_parameters() -> None:
    class RequiredQuery(BaseModel):
        value: str

    declared = contract(
        method="GET",
        path="/required",
        query=RequiredQuery,
        response=RequiredQuery,
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": request.url.params["value"]})

    http = httpx.AsyncClient(
        base_url="http://testserver",
        params={"value": "inherited"},
        transport=httpx.MockTransport(respond),
    )
    async with Client(http=http) as client:
        result = await client.call(declared)
    await http.aclose()

    assert result == RequiredQuery(value="inherited")


async def test_per_call_query_cannot_silently_reveal_inherited_parameters() -> None:
    class OptionalQuery(BaseModel):
        trace_id: str | None = None

    declared = contract(
        method="GET",
        path="/query",
        query=OptionalQuery,
        status=204,
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    http = httpx.AsyncClient(
        base_url="http://testserver",
        params={"trace_id": "inherited"},
        transport=httpx.MockTransport(respond),
    )
    async with Client(http=http) as client:
        with pytest.raises(ConfigurationError, match="changes the validated value"):
            await client.call(declared, query=OptionalQuery())
    await http.aclose()

    assert requests == []


@pytest.mark.parametrize("slot", ["query", "headers"])
async def test_call_rejects_omitted_required_parameter_groups_before_io(
    slot: str,
) -> None:
    class RequiredParameters(BaseModel):
        value: str

    if slot == "query":
        declared = contract(
            method="GET",
            path="/required",
            query=RequiredParameters,
            status=204,
        )
    else:
        declared = contract(
            method="GET",
            path="/required",
            headers=RequiredParameters,
            status=204,
        )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValidationError):
            await client.call(declared)

    assert requests == []


async def test_call_returns_none_for_empty_response(client: Client) -> None:
    assert await client.call(clear_contract) is None


async def test_call_sends_headers_with_hyphenated_names(client: Client) -> None:
    result = await client.call(
        headers_contract,
        headers=ClientHeaders(x_api_key="abc", accept_language="fr"),
    )

    assert result == "abc/fr"


async def test_call_uses_pydantic_aliases_as_wire_names() -> None:
    class AliasedParams(BaseModel):
        item_id: str = Field(alias="id")

    class AliasedQuery(BaseModel):
        tags: list[str] = Field(alias="labels")

    class AliasedHeaders(BaseModel):
        api_key: str = Field(alias="X-API-Key")

    class AliasedItem(BaseModel):
        title: str = Field(alias="wireTitle")

    declared = contract(
        method="POST",
        path="/aliased/{id}",
        params=AliasedParams,
        query=AliasedQuery,
        headers=AliasedHeaders,
        request=AliasedItem,
        response=AliasedItem,
    )
    received: list[tuple[str, list[str], str, str]] = []

    async def echo(
        params: AliasedParams,
        query: AliasedQuery,
        headers: AliasedHeaders,
        request: AliasedItem,
        context: Context,
    ) -> AliasedItem:
        received.append((params.item_id, query.tags, headers.api_key, request.title))
        return request

    app = create_app(routes=route_group(route(declared, echo)), context_factory=Context)

    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        result = await tenchi_client.call(
            declared,
            params=AliasedParams(id="42"),
            query=AliasedQuery(labels=["featured"]),
            headers=AliasedHeaders.model_validate({"X-API-Key": "secret"}),
            request=AliasedItem(wireTitle="milk"),
        )

    assert result == AliasedItem(wireTitle="milk")
    assert received == [("42", ["featured"], "secret", "milk")]


async def test_header_alias_modes_can_normalize_to_the_same_wire_name() -> None:
    class AliasedHeaders(BaseModel):
        trace: str = Field(
            validation_alias="X-Trace",
            serialization_alias="x_trace",
        )

    declared = contract(
        method="GET",
        path="/header-alias-modes",
        headers=AliasedHeaders,
        response=str,
    )

    async def echo(headers: AliasedHeaders, context: Context) -> str:
        return headers.trace

    app = create_app(routes=route_group(route(declared, echo)), context_factory=Context)

    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        result = await tenchi_client.call(
            declared,
            headers=AliasedHeaders.model_validate({"X-Trace": "trace-1"}),
        )

    assert result == "trace-1"


async def test_call_round_trips_fixed_tuple_query_fields() -> None:
    class FixedQuery(BaseModel):
        values: tuple[str, int]

    declared = contract(
        method="GET",
        path="/fixed-query",
        query=FixedQuery,
        response=FixedQuery,
    )

    async def echo(query: FixedQuery, context: Context) -> FixedQuery:
        return query

    app = create_app(routes=route_group(route(declared, echo)), context_factory=Context)

    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        result = await tenchi_client.call(
            declared,
            query=FixedQuery(values=("first", 2)),
        )

    assert result == FixedQuery(values=("first", 2))


async def test_call_uses_validation_requiredness_for_nullable_query_fields() -> None:
    class OptionalQuery(BaseModel):
        model_config = ConfigDict(json_schema_serialization_defaults_required=True)

        value: str | None = None

    declared = contract(
        method="GET",
        path="/optional-query",
        query=OptionalQuery,
        response=OptionalQuery,
    )

    async def echo(query: OptionalQuery, context: Context) -> OptionalQuery:
        return query

    app = create_app(routes=route_group(route(declared, echo)), context_factory=Context)

    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        result = await tenchi_client.call(declared, query=OptionalQuery())

    assert result == OptionalQuery(value=None)


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


@pytest.mark.parametrize("source", [None, "framework"])
async def test_matching_non_app_error_envelope_remains_unexpected(
    source: str | None,
) -> None:
    declared = contract(
        method="GET",
        path="/collision",
        response=Item,
        errors=(validation_error,),
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        headers = {ERROR_SOURCE_HEADER: source} if source is not None else None
        return httpx.Response(
            422,
            json={"code": "VALIDATION_ERROR", "message": "Bad input"},
            headers=headers,
        )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UnexpectedResponseError) as excinfo:
            await client.call(declared)

    assert excinfo.value.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"code": "ITEM_MISSING"},
        {"code": "ITEM_MISSING", "message": 42},
    ],
)
async def test_malformed_app_error_envelope_remains_unexpected(
    body: dict[str, object],
) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json=body,
            headers={ERROR_SOURCE_HEADER: "app"},
        )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UnexpectedResponseError):
            await client.call(get_item_contract, params=ItemParams(item_id="missing"))


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


async def test_omitted_header_model_defaults_override_httpx_defaults() -> None:
    class AcceptHeaders(BaseModel):
        accept: str = "application/json"

    declared = contract(
        method="GET",
        path="/headers",
        headers=AcceptHeaders,
        status=204,
    )
    received: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        received.append(request.headers["accept"])
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        await client.call(declared)

    assert received == ["application/json"]


async def test_nullable_header_cannot_hide_an_httpx_transport_default() -> None:
    class OptionalAcceptHeaders(BaseModel):
        accept: str | None = None

    declared = contract(
        method="GET",
        path="/headers",
        headers=OptionalAcceptHeaders,
        status=204,
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match="changes the validated value"):
            await client.call(declared)

    assert requests == []


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


async def test_per_call_headers_cannot_silently_reveal_client_defaults() -> None:
    class OptionalHeaders(BaseModel):
        trace_id: str | None = Field(default=None, alias="X-Trace-ID")

    declared = contract(
        method="GET",
        path="/headers",
        headers=OptionalHeaders,
        status=204,
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with Client(
        transport=httpx.MockTransport(respond),
        headers={"x-trace-id": "inherited"},
    ) as client:
        with pytest.raises(ConfigurationError, match="changes the validated value"):
            await client.call(declared, headers=OptionalHeaders())

    assert requests == []


def test_client_constructor_validation() -> None:
    with pytest.raises(ValueError, match="requires base_url="):
        Client()

    with pytest.raises(ValueError, match="mutually exclusive"):
        Client(base_url="http://x", http=httpx.AsyncClient())

    with pytest.raises(ValueError, match="mutually exclusive"):
        Client(headers={"a": "b"}, http=httpx.AsyncClient())


def test_client_rejects_malformed_shared_errors() -> None:
    with pytest.raises(ConfigurationError, match=r"errors\[0\].*ErrorDef"):
        Client(
            base_url="http://example.test",
            errors=("UNAUTHORIZED",),  # type: ignore[arg-type]
        )


async def test_client_rejects_contract_and_shared_error_conflicts_before_io() -> None:
    contract_error = ErrorDef(code="CONFLICT", status=409, message="Contract")
    shared_error = ErrorDef(code="CONFLICT", status=409, message="Shared")
    declared = contract(
        method="GET", path="/conflict", response=Item, errors=(contract_error,)
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"name": "x"})

    async with Client(
        transport=httpx.MockTransport(respond), errors=(shared_error,)
    ) as client:
        with pytest.raises(ConfigurationError, match=r"conflicting ErrorDef.*CONFLICT"):
            await client.call(declared)

    assert requests == []


@pytest.mark.parametrize("slot", ["query", "headers"])
async def test_client_preflights_omitted_optional_input_types(slot: str) -> None:
    class Unsupported:
        pass

    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    declared = (
        contract(method="GET", path="/invalid-input", status=204, query=Unsupported)
        if slot == "query"
        else contract(
            method="GET", path="/invalid-input", status=204, headers=Unsupported
        )
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match=rf"{slot} type.*Unsupported"):
            await client.call(declared)

    assert calls == []


@pytest.mark.parametrize("slot", ["query", "headers"])
async def test_client_rejects_scalar_optional_input_types_before_io(slot: str) -> None:
    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    declared = (
        contract(method="GET", path="/scalar-input", status=204, query=int)
        if slot == "query"
        else contract(method="GET", path="/scalar-input", status=204, headers=int)
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match=rf"{slot} type.*object"):
            await client.call(declared)

    assert calls == []


@pytest.mark.parametrize("slot", ["query", "headers"])
async def test_client_rejects_parameters_without_a_wire_encoding(slot: str) -> None:
    class NestedQuery(BaseModel):
        filters: dict[str, str]

    class RepeatedHeaders(BaseModel):
        x_values: list[str]

    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    declared = (
        contract(method="GET", path="/unsupported-query", query=NestedQuery, status=204)
        if slot == "query"
        else contract(
            method="GET",
            path="/unsupported-headers",
            headers=RepeatedHeaders,
            status=204,
        )
    )
    value = (
        NestedQuery(filters={"status": "open"})
        if slot == "query"
        else RepeatedHeaders(x_values=["one", "two"])
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match="must serialize as"):
            if slot == "query":
                await client.call(declared, query=value)
            else:
                await client.call(declared, headers=value)

    assert calls == []


@pytest.mark.parametrize(
    "kind", ["nested serializer", "cardinality serializer", "computed field"]
)
async def test_client_rejects_parameter_serialization_schema_drift_before_io(
    kind: str,
) -> None:
    def serialize_nested(value: int) -> dict[str, int]:
        return {"value": value}

    class SerializedQuery(BaseModel):
        value: Annotated[
            int,
            PlainSerializer(serialize_nested, return_type=dict[str, int]),
        ]

    def serialize_values(values: list[int]) -> str:
        return ",".join(str(value) for value in values)

    class FlattenedQuery(BaseModel):
        values: Annotated[
            list[int],
            PlainSerializer(serialize_values, return_type=str),
        ]

    class ComputedQuery(BaseModel):
        value: int

        @computed_field
        @property
        def doubled(self) -> int:
            return self.value * 2

    if kind == "nested serializer":
        query_type = SerializedQuery
        query = SerializedQuery(value=1)
    elif kind == "cardinality serializer":
        query_type = FlattenedQuery
        query = FlattenedQuery(values=[1, 2])
    else:
        query_type = ComputedQuery
        query = ComputedQuery(value=1)
    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    declared = contract(
        method="GET",
        path="/serialization-drift",
        query=query_type,
        status=204,
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match=r"serializ|field names"):
            await client.call(declared, query=query)

    assert calls == []


@pytest.mark.parametrize("slot", ["params", "query", "headers"])
async def test_client_rejects_undeclared_serialized_parameter_fields_before_io(
    slot: str,
) -> None:
    class InjectedParameters(BaseModel):
        value: str

        @model_serializer
        def serialize_model(  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
            self,
        ):
            return {"value": self.value, "injected": "not-declared"}

    if slot == "params":
        declared = contract(
            method="GET",
            path="/items/{value}",
            params=InjectedParameters,
            status=204,
        )
    elif slot == "query":
        declared = contract(
            method="GET",
            path="/items",
            query=InjectedParameters,
            status=204,
        )
    else:
        declared = contract(
            method="GET",
            path="/items",
            headers=InjectedParameters,
            status=204,
        )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    value = InjectedParameters(value="one")
    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match="undeclared HTTP parameter"):
            if slot == "params":
                await client.call(declared, params=value)
            elif slot == "query":
                await client.call(declared, query=value)
            else:
                await client.call(declared, headers=value)

    assert requests == []


async def test_client_rejects_non_object_parameter_serialization_before_io() -> None:
    class ListSerializedQuery(BaseModel):
        value: str

        @model_serializer
        def serialize_model(  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
            self,
        ):
            return [self.value]

    declared = contract(
        method="GET",
        path="/items",
        query=ListSerializedQuery,
        status=204,
    )
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ConfigurationError, match="must produce an object"):
            await client.call(declared, query=ListSerializedQuery(value="one"))

    assert requests == []


async def test_client_rejects_query_values_that_do_not_round_trip_before_io() -> None:
    def increment(value: int) -> str:
        return str(value + 1)

    def collapse(values: list[str]) -> list[str]:
        values[:] = ["changed"]
        return values

    class SecretQuery(BaseModel):
        value: SecretStr

    class ChangedQuery(BaseModel):
        value: Annotated[int, PlainSerializer(increment, return_type=str)]

    class RequiredListQuery(BaseModel):
        values: list[str]

    class NullableListQuery(BaseModel):
        values: list[str] | None = None

    class MutatingQuery(BaseModel):
        values: Annotated[
            list[str],
            PlainSerializer(collapse, return_type=list[str]),
        ]

    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    mutating = MutatingQuery(values=["one", "two"])
    cases = (
        (SecretQuery, SecretQuery(value=SecretStr("secret"))),
        (ChangedQuery, ChangedQuery(value=1)),
        (RequiredListQuery, RequiredListQuery(values=[])),
        (NullableListQuery, NullableListQuery(values=[])),
        (MutatingQuery, mutating),
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        for query_type, value in cases:
            declared = contract(
                method="GET",
                path="/query-round-trip",
                query=query_type,
                status=204,
            )
            with pytest.raises(ConfigurationError, match="must round-trip"):
                await client.call(declared, query=value)

    assert calls == []
    assert mutating.values == ["one", "two"]


async def test_client_rejects_header_values_that_do_not_round_trip_before_io() -> None:
    def increment(value: int) -> str:
        return str(value + 1)

    class SecretHeaders(BaseModel):
        value: SecretStr

    class ChangedHeaders(BaseModel):
        value: Annotated[int, PlainSerializer(increment, return_type=str)]

    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    cases = (
        (SecretHeaders, SecretHeaders(value=SecretStr("secret"))),
        (ChangedHeaders, ChangedHeaders(value=1)),
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        for headers_type, value in cases:
            declared = contract(
                method="GET",
                path="/header-round-trip",
                headers=headers_type,
                status=204,
            )
            with pytest.raises(ConfigurationError, match="must round-trip"):
                await client.call(declared, headers=value)

    assert calls == []


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("line\nbreak", "must not contain CR or LF"),
        (" padded", "must not start or end with whitespace"),
        ("value\x00", "must not contain control characters"),
        ("café", "must be ASCII encodable"),
    ],
)
async def test_client_rejects_invalid_http_header_values_before_io(
    value: str,
    message: str,
) -> None:
    class UnsafeHeaders(BaseModel):
        x_value: str

    declared = contract(
        method="GET",
        path="/unsafe-header",
        headers=UnsafeHeaders,
        status=204,
    )
    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError, match=message):
            await client.call(declared, headers=UnsafeHeaders(x_value=value))

    assert calls == []


async def test_client_allows_parameter_serialization_that_round_trips() -> None:
    def render_int(value: int) -> str:
        return str(value)

    class DefaultListQuery(BaseModel):
        values: list[str] = []

    class RenderedHeaders(BaseModel):
        value: Annotated[int, PlainSerializer(render_int, return_type=str)]

    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    query_contract = contract(
        method="GET",
        path="/default-list",
        query=DefaultListQuery,
        status=204,
    )
    header_contract = contract(
        method="GET",
        path="/rendered-header",
        headers=RenderedHeaders,
        status=204,
    )
    async with Client(transport=httpx.MockTransport(respond)) as client:
        assert (
            await client.call(query_contract, query=DefaultListQuery(values=[])) is None
        )
        assert (
            await client.call(header_contract, headers=RenderedHeaders(value=1)) is None
        )

    assert [request.url.path for request in calls] == [
        "/default-list",
        "/rendered-header",
    ]


async def test_client_rejects_ambiguous_or_unencodable_parameter_unions() -> None:
    class MixedCardinalityQuery(BaseModel):
        value: str | list[str]

    class NullableItemQuery(BaseModel):
        values: list[str | None]

    class NullHeader(BaseModel):
        x_value: None

    class RequiredNullableQuery(BaseModel):
        value: str | None

    class RequiredNullableHeaders(BaseModel):
        x_value: str | None

    calls: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(204)

    declarations = (
        (
            contract(
                method="GET",
                path="/mixed-cardinality",
                query=MixedCardinalityQuery,
                status=204,
            ),
            "query",
            MixedCardinalityQuery(value="one"),
        ),
        (
            contract(
                method="GET",
                path="/nullable-items",
                query=NullableItemQuery,
                status=204,
            ),
            "query",
            NullableItemQuery(values=["one", None]),
        ),
        (
            contract(
                method="GET",
                path="/null-header",
                headers=NullHeader,
                status=204,
            ),
            "headers",
            NullHeader(x_value=None),
        ),
        (
            contract(
                method="GET",
                path="/required-nullable-query",
                query=RequiredNullableQuery,
                status=204,
            ),
            "query",
            RequiredNullableQuery(value=None),
        ),
        (
            contract(
                method="GET",
                path="/required-nullable-header",
                headers=RequiredNullableHeaders,
                status=204,
            ),
            "headers",
            RequiredNullableHeaders(x_value=None),
        ),
    )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        for declared, slot, value in declarations:
            with pytest.raises(
                ConfigurationError,
                match=r"must serialize as|required but accepts null",
            ):
                if slot == "query":
                    await client.call(declared, query=value)
                else:
                    await client.call(declared, headers=value)

    assert calls == []


async def test_structured_json_media_type_round_trips() -> None:
    declared = contract(
        method="POST",
        path="/vendor-json",
        request=Item,
        request_media_type="application/vnd.tenchi+json",
        response=Item,
        response_media_type="application/vnd.tenchi+json",
    )

    async def echo(request: Item, context: Context) -> Item:
        return request

    app = create_app(routes=route_group(route(declared, echo)), context_factory=Context)

    async with Client(transport=httpx.ASGITransport(app=app)) as client:
        result = await client.call(declared, request=Item(name="x"))

    assert result == Item(name="x")


@pytest.mark.parametrize("charset", ["iso-8859-1", "utf-16"])
async def test_typed_text_round_trip_honors_declared_charset(charset: str) -> None:
    media_type = f"text/plain; charset={charset}"
    declared = contract(
        method="POST",
        path="/text",
        request=str,
        request_media_type=media_type,
        response=str,
        response_media_type=media_type,
    )

    async def shout(request: str, context: Context) -> str:
        return request.upper()

    app = create_app(
        routes=route_group(route(declared, shout)), context_factory=Context
    )

    async with Client(transport=httpx.ASGITransport(app=app)) as client:
        result = await client.call_with_response(declared, request="café")

    assert result.body == "CAFÉ"
    assert result.http_response.content == "CAFÉ".encode(charset)
    assert result.http_response.headers["content-type"] == media_type


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/json; charset*=utf-8''utf-8"],
)
async def test_client_rejects_success_with_wrong_response_media_type(
    content_type: str | None,
) -> None:
    declared = contract(method="GET", path="/item", response=Item)

    async def respond(request: httpx.Request) -> httpx.Response:
        headers = {"content-type": content_type} if content_type is not None else None
        return httpx.Response(200, content=b'{"name":"x"}', headers=headers)

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UnexpectedResponseError, match="content type") as excinfo:
            await client.call(declared)

    assert excinfo.value.reason is not None
    assert "application/json" in excinfo.value.reason


async def test_client_accepts_additional_response_media_type_parameters() -> None:
    declared = contract(method="GET", path="/item", response=Item)

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"name":"x"}',
            headers={"content-type": "application/json; charset=UTF-8"},
        )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        result = await client.call(declared)

    assert result == Item(name="x")


async def test_client_decodes_text_using_the_wire_charset() -> None:
    declared = contract(
        method="GET",
        path="/text",
        response=str,
        response_media_type="text/plain",
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content="café".encode("iso-8859-1"),
            headers={"content-type": "text/plain; charset=iso-8859-1"},
        )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        result = await client.call(declared)

    assert result == "café"


@pytest.mark.parametrize(
    ("content", "content_type", "reason"),
    [
        (b"\xff", "text/plain; charset=ascii", "not valid for charset 'ascii'"),
        (
            b"text",
            "text/plain; charset=not-a-codec",
            "unsupported charset 'not-a-codec'",
        ),
    ],
)
async def test_client_rejects_text_that_violates_the_wire_charset(
    content: bytes, content_type: str, reason: str
) -> None:
    declared = contract(
        method="GET",
        path="/text",
        response=str,
        response_media_type="text/plain",
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=content,
            headers={"content-type": content_type},
        )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UnexpectedResponseError, match=reason):
            await client.call(declared)


async def test_client_rejects_error_with_wrong_response_media_type() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            content=b'{"code":"ITEM_MISSING","message":"Missing"}',
            headers={
                ERROR_SOURCE_HEADER: "app",
                "content-type": "text/plain",
            },
        )

    async with Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(UnexpectedResponseError, match="content type"):
            await client.call(
                get_item_contract,
                params=ItemParams(item_id="missing"),
            )


throttled = ErrorDef(
    code="THROTTLED", status=429, message="Slow down", headers=("Retry-After",)
)
limited_contract = contract(
    method="GET", path="/limited", response=Item, errors=(throttled,)
)


async def test_declared_error_headers_reach_the_raised_error() -> None:
    async def rate_limited(context: Context) -> Item:
        raise AppError(throttled, headers={"Retry-After": "30"})

    app = create_app(
        routes=route_group(route(limited_contract, rate_limited)),
        context_factory=Context,
    )

    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        with pytest.raises(AppError) as excinfo:
            await tenchi_client.call(limited_contract)

    assert excinfo.value.headers["Retry-After"] == "30"


async def test_undeclared_inputs_are_rejected_not_dropped(client: Client) -> None:
    with pytest.raises(TypeError, match="does not declare request="):
        await client.call(clear_contract, request=Item(name="x"))
    with pytest.raises(TypeError, match="does not declare request="):
        await client.call(clear_contract, request=None)
    with pytest.raises(TypeError, match="does not declare query="):
        await client.call(create_item_contract, query={"term": "x"})


async def test_empty_path_param_is_rejected(client: Client) -> None:
    class OptionalParams(BaseModel):
        item_id: str = ""

    optional_contract = contract(
        method="GET",
        path="/items/{item_id}",
        params=OptionalParams,
        response=Item,
    )

    with pytest.raises(ValueError, match="must be a non-empty value"):
        await client.call(optional_contract, params=OptionalParams())


async def test_non_json_media_with_model_request_is_rejected(client: Client) -> None:
    bad_contract = contract(
        method="POST",
        path="/items",
        request=Item,
        request_media_type="application/xml",
        response=Item,
    )

    with pytest.raises(TypeError, match="cannot encode Item as application/xml"):
        await client.call(bad_contract, request=Item(name="x"))
