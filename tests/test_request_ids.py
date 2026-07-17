"""Request ids: generated or honored, echoed everywhere, logged."""

from dataclasses import dataclass

import pytest

from tenchi.contracts import contract
from tenchi.errors import REQUEST_ID_HEADER, AppError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import RequestInfo, create_app
from tenchi.testing import open_http


@dataclass(frozen=True, slots=True)
class Context:
    pass


boom = ErrorDef(code="BOOM", status=409, message="Boom")

ping_contract = contract(method="GET", path="/ping", response=str)
fail_contract = contract(method="GET", path="/fail", response=str, errors=(boom,))


async def ping(context: Context) -> str:
    return "pong"


async def fail(context: Context) -> str:
    raise AppError(boom)


def make_app(hooks: list[object] | None = None):
    return create_app(
        routes=route_group(route(ping_contract, ping), route(fail_contract, fail)),
        context_factory=Context,
        hooks=hooks or [],  # pyright: ignore[reportArgumentType]
    )


async def test_success_responses_carry_a_generated_request_id() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/ping")

    request_id = response.headers[REQUEST_ID_HEADER]
    assert len(request_id) == 32  # uuid4().hex


async def test_inbound_request_id_is_honored() -> None:
    async with open_http(make_app()) as http:
        success = await http.get("/ping", headers={REQUEST_ID_HEADER: "trace-42"})
        failure = await http.get("/fail", headers={REQUEST_ID_HEADER: "trace-42"})

    assert success.headers[REQUEST_ID_HEADER] == "trace-42"
    assert failure.headers[REQUEST_ID_HEADER] == "trace-42"
    assert failure.json()["request_id"] == "trace-42"


async def test_error_envelope_and_header_match() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/fail")

    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


async def test_unmatched_routes_carry_request_ids_too() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/nope", headers={REQUEST_ID_HEADER: "trace-404"})

    assert response.headers[REQUEST_ID_HEADER] == "trace-404"
    assert response.json()["request_id"] == "trace-404"


async def test_oversized_inbound_ids_are_replaced() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/ping", headers={REQUEST_ID_HEADER: "x" * 500})

    assert response.headers[REQUEST_ID_HEADER] != "x" * 500
    assert len(response.headers[REQUEST_ID_HEADER]) == 32


@pytest.mark.parametrize(
    "inbound",
    ["trace\r\nx-injected: yes", " trace", "trace\x7f"],
)
async def test_injection_prone_inbound_ids_are_replaced(inbound: str) -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/ping", headers={REQUEST_ID_HEADER: inbound})

    request_id = response.headers[REQUEST_ID_HEADER]
    assert request_id != inbound
    assert len(request_id) == 32


async def test_hooks_see_the_request_id() -> None:
    seen: list[str] = []

    def observe(info: RequestInfo, context: Context) -> None:
        seen.append(info.request_id)

    async with open_http(make_app(hooks=[observe])) as http:
        await http.get("/ping", headers={REQUEST_ID_HEADER: "trace-7"})

    assert seen == ["trace-7"]
