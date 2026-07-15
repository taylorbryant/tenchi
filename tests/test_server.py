"""Framework-level HTTP behavior, exercised through a minimal inline app."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import BaseModel, field_validator

from tenchi.contracts import contract
from tenchi.errors import ERROR_SOURCE_HEADER, AppError, ErrorDef
from tenchi.routes import RouteGroup, route, route_group
from tenchi.server import create_app


class Item(BaseModel):
    name: str


class Guarded(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def no_x(cls, value: str) -> str:
        if "x" in value:
            raise ValueError("no x allowed")
        return value


class SearchQuery(BaseModel):
    term: str = ""
    limit: int = 10
    tags: list[str] = []


class ClientHeaders(BaseModel):
    x_api_key: str
    accept_language: str = "en"


@dataclass(frozen=True, slots=True)
class Context:
    request_id: int


boom = ErrorDef(code="BOOM", status=409, message="Boom")
throttled = ErrorDef(
    code="THROTTLED", status=429, message="Slow down", headers=("Retry-After",)
)


async def make_client(routes: RouteGroup) -> httpx.AsyncClient:
    counter = iter(range(1_000_000))

    async def context_factory() -> Context:
        return Context(request_id=next(counter))

    app = create_app(routes=routes, context_factory=context_factory)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async def echo(request: Item, context: Context) -> Item:
        return request

    async def no_content(context: Context) -> None:
        return None

    async def declared_error(context: Context) -> Item:
        raise AppError(boom, details={"why": "declared"})

    async def undeclared_error(context: Context) -> Item:
        raise AppError(boom)

    async def crash(context: Context) -> Item:
        raise RuntimeError("crash")

    async def wrong_shape(context: Context) -> Item:
        return "not an item"  # type: ignore[return-value]

    async def whoami(context: Context) -> int:
        return context.request_id

    async def search(query: SearchQuery, context: Context) -> SearchQuery:
        return query

    async def shout(request: str, context: Context) -> str:
        return request.upper()

    async def checksum(request: bytes, context: Context) -> bytes:
        return bytes([sum(request) % 256])

    async def rate_limited(context: Context) -> Item:
        raise AppError(throttled, headers={"Retry-After": "30"})

    async def read_headers(headers: ClientHeaders, context: Context) -> str:
        return f"{headers.x_api_key}/{headers.accept_language}"

    async def guarded_echo(request: Guarded, context: Context) -> Guarded:
        return request

    async def timed_error(context: Context) -> Item:
        raise AppError(boom, details={"at": datetime(2026, 1, 1, tzinfo=UTC)})

    routes = route_group(
        route(
            contract(method="POST", path="/echo", request=Item, response=Item),
            echo,
        ),
        route(
            contract(method="DELETE", path="/empty", status=204),
            no_content,
        ),
        route(
            contract(method="POST", path="/declared", response=Item, errors=(boom,)),
            declared_error,
        ),
        route(
            contract(method="POST", path="/undeclared", response=Item),
            undeclared_error,
        ),
        route(
            contract(method="POST", path="/crash", response=Item),
            crash,
        ),
        route(
            contract(method="GET", path="/wrong-shape", response=Item),
            wrong_shape,
        ),
        route(
            contract(method="GET", path="/whoami", response=int),
            whoami,
        ),
        route(
            contract(
                method="GET",
                path="/search",
                query=SearchQuery,
                response=SearchQuery,
            ),
            search,
        ),
        route(
            contract(
                method="POST",
                path="/shout",
                request=str,
                request_media_type="text/plain",
                response=str,
                response_media_type="text/plain",
            ),
            shout,
        ),
        route(
            contract(
                method="POST",
                path="/checksum",
                request=bytes,
                request_media_type="application/octet-stream",
                response=bytes,
                response_media_type="application/octet-stream",
            ),
            checksum,
        ),
        route(
            contract(method="GET", path="/limited", response=Item, errors=(throttled,)),
            rate_limited,
        ),
        route(
            contract(
                method="GET",
                path="/headers",
                headers=ClientHeaders,
                response=str,
            ),
            read_headers,
        ),
        route(
            contract(method="POST", path="/guarded", request=Guarded, response=Guarded),
            guarded_echo,
        ),
        route(
            contract(method="GET", path="/timed", response=Item, errors=(boom,)),
            timed_error,
        ),
    )
    async with await make_client(routes) as client:
        yield client


async def test_dispatch_validates_and_echoes(client: httpx.AsyncClient) -> None:
    response = await client.post("/echo", json={"name": "x"})

    assert response.status_code == 200
    assert response.json() == {"name": "x"}
    assert ERROR_SOURCE_HEADER not in response.headers


async def test_invalid_body_maps_to_framework_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/echo", json={"name": 1})

    assert response.status_code == 422
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_contract_without_response_returns_empty_body(
    client: httpx.AsyncClient,
) -> None:
    response = await client.delete("/empty")

    assert response.status_code == 204
    assert response.content == b""


async def test_declared_error_maps_to_its_status(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/declared")

    assert response.status_code == 409
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    body = response.json()
    assert body.pop("request_id") == response.headers["x-request-id"]
    assert body == {
        "code": "BOOM",
        "message": "Boom",
        "details": {"why": "declared"},
    }


async def test_undeclared_app_error_becomes_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/undeclared")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_unexpected_exception_becomes_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/crash")

    assert response.status_code == 500
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_response_not_matching_contract_becomes_internal_error(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/wrong-shape")

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_SERVER_ERROR"


async def test_query_params_are_coerced(client: httpx.AsyncClient) -> None:
    response = await client.get("/search?term=milk&limit=5&tags=a&tags=b")

    assert response.status_code == 200
    assert response.json() == {"term": "milk", "limit": 5, "tags": ["a", "b"]}


async def test_single_repeated_query_value_still_makes_a_list(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/search?tags=a")

    assert response.status_code == 200
    assert response.json()["tags"] == ["a"]


async def test_custom_validator_failure_is_a_422(client: httpx.AsyncClient) -> None:
    response = await client.post("/guarded", json={"name": "axe"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "no x allowed" in body["details"][0]["msg"]


async def test_non_json_error_details_are_coerced_not_crashed(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/timed")

    assert response.status_code == 409
    assert response.json()["details"] == {"at": "2026-01-01T00:00:00Z"}


async def test_query_defaults_apply_when_absent(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/search")

    assert response.status_code == 200
    assert response.json() == {"term": "", "limit": 10, "tags": []}


async def test_invalid_query_maps_to_framework_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/search?limit=lots")

    assert response.status_code == 422
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_text_media_type_round_trips(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/shout", content="hello", headers={"content-type": "text/plain"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "HELLO"


async def test_invalid_utf8_text_body_maps_to_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/shout", content=b"\xff\xfe")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_binary_media_type_round_trips(client: httpx.AsyncClient) -> None:
    response = await client.post("/checksum", content=b"\x01\x02\x03")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == b"\x06"


async def test_app_error_headers_reach_the_response(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/limited")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert response.headers[ERROR_SOURCE_HEADER] == "app"
    assert response.json()["code"] == "THROTTLED"


async def test_headers_validate_with_normalized_names(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/headers",
        headers={"X-Api-Key": "abc", "Accept-Language": "fr"},
    )

    assert response.status_code == 200
    assert response.json() == "abc/fr"


async def test_repeated_headers_keep_the_last_value(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/headers",
        headers=[
            ("x-api-key", "first"),
            ("x-api-key", "last"),
        ],
    )

    assert response.status_code == 200
    assert response.json() == "last/en"


async def test_header_defaults_apply_when_absent(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/headers", headers={"x-api-key": "abc"})

    assert response.json() == "abc/en"


async def test_missing_required_header_maps_to_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/headers")

    assert response.status_code == 422
    assert response.headers[ERROR_SOURCE_HEADER] == "framework"
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["x_api_key"]


async def test_context_factory_runs_per_request(
    client: httpx.AsyncClient,
) -> None:
    first = await client.get("/whoami")
    second = await client.get("/whoami")

    assert first.json() != second.json()


async def test_405_keeps_the_allow_header(client: httpx.AsyncClient) -> None:
    response = await client.delete("/echo")

    assert response.status_code == 405
    assert "POST" in response.headers["allow"]


def test_route_group_rejects_trailing_slash_prefix() -> None:
    with pytest.raises(ValueError, match="must not end with '/'"):
        route_group(prefix="/")


async def test_create_app_rejects_duplicate_routes() -> None:
    async def use_case(context: Context) -> Item:
        return Item(name="x")

    declared = contract(method="GET", path="/dup", response=Item)
    routes = route_group(route(declared, use_case), route(declared, use_case))

    with pytest.raises(ValueError, match="duplicate route GET /dup"):
        create_app(routes=routes, context_factory=lambda: Context(request_id=0))
