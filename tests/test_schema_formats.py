"""Optional jsonschema format packages must not change Tenchi boundaries."""

from datetime import UTC, datetime, time

import pytest
from jsonschema import FormatChecker
from mcp.client import Client
from pydantic import AwareDatetime, BaseModel, ValidationError

from tenchi.contracts import contract
from tenchi.jobs import (
    create_job_dispatcher,
    job,
    job_group,
    job_handler,
    job_message,
)
from tenchi.mcp import create_tool_mcp_server
from tenchi.openapi import openapi_schema
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http
from tenchi.tools import create_tool_runner, tool, tool_group, tool_handler


class Moment(BaseModel):
    when: datetime
    at: time


async def echo(request: Moment, context: object) -> Moment:
    return request


@pytest.fixture(autouse=True, params=["absent", "installed"])
def optional_format_checkers(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(value: object) -> bool:
        return False

    # Simulate optional checkers being unavailable or enforcing a stricter
    # grammar than Pydantic, without depending on the developer's packages.
    monkeypatch.setattr(
        FormatChecker,
        "checkers",
        {}
        if request.param == "absent"
        else {
            "date-time": (reject, ()),
            "time": (reject, ()),
        },
    )


@pytest.fixture(params=[None, UTC], ids=["naive", "aware"])
def moment(request: pytest.FixtureRequest) -> Moment:
    return Moment(
        when=datetime(2024, 1, 1, 12, tzinfo=request.param),
        at=time(12, tzinfo=request.param),
    )


async def test_http_temporal_values_follow_pydantic(moment: Moment) -> None:
    declared = contract(method="POST", path="/moments", request=Moment, response=Moment)
    app = create_app(
        routes=route_group(route(declared, echo)), context_factory=lambda: object()
    )

    async with open_http(app) as client:
        response = await client.post("/moments", json=moment.model_dump(mode="json"))
        invalid = await client.post("/moments", json={"when": "invalid", "at": "12:00"})

    assert response.status_code == 200
    assert Moment.model_validate_json(response.content) == moment
    assert invalid.status_code == 422


def test_openapi_temporal_examples_follow_pydantic(moment: Moment) -> None:
    declared = contract(
        method="POST",
        path="/moments",
        request=Moment,
        response=Moment,
        request_examples={"moment": moment},
        response_examples={"moment": moment},
    )
    schema = openapi_schema(
        route_group(route(declared, echo)), title="Moments", version="1"
    )
    operation = schema["paths"]["/moments"]["post"]
    expected = {"moment": {"value": moment.model_dump(mode="json")}}
    assert (
        operation["requestBody"]["content"]["application/json"]["examples"] == expected
    )
    assert (
        operation["responses"]["200"]["content"]["application/json"]["examples"]
        == expected
    )


async def test_job_temporal_messages_follow_pydantic(moment: Moment) -> None:
    declaration = job("moments.echo", request=Moment, result=Moment)
    message = job_message(declaration, moment)
    dispatcher = create_job_dispatcher(jobs=job_group(job_handler(declaration, echo)))

    result = await dispatcher.dispatch(
        message.name, payload_json=message.payload_json, context=object()
    )

    assert result == moment


async def test_tool_temporal_results_follow_pydantic(moment: Moment) -> None:
    declaration = tool(
        "moments.echo",
        request=Moment,
        result=Moment,
        description="Echo a moment.",
        read_only=True,
        destructive=False,
    )
    runner = create_tool_runner(
        tools=tool_group(tool_handler(declaration, echo)),
        context_factory=lambda: object(),
    )

    assert await runner.call("moments.echo", input=moment) == moment


async def test_mcp_temporal_inputs_and_results_follow_pydantic(moment: Moment) -> None:
    declaration = tool(
        "moments.echo",
        request=Moment,
        result=Moment,
        description="Echo a moment.",
        read_only=True,
        destructive=False,
    )
    tools = tool_group(tool_handler(declaration, echo))
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: "principal",
        runner_factory=lambda principal: create_tool_runner(
            tools=tools, context_factory=lambda: object()
        ),
    )

    async with Client(server) as session:
        result = await session.call_tool("moments.echo", moment.model_dump(mode="json"))
        invalid = await session.call_tool(
            "moments.echo", {"when": "invalid", "at": "12:00"}
        )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["result"] == moment.model_dump(mode="json")
    assert invalid.is_error is True


def test_explicit_pydantic_timezone_requirement_is_still_enforced() -> None:
    class AwareMoment(BaseModel):
        when: AwareDatetime

    declaration = job("moments.aware", request=AwareMoment, result=None)
    with pytest.raises(ValidationError):
        job_message(declaration, {"when": "2024-01-01T12:00:00"})  # pyright: ignore[reportArgumentType]
