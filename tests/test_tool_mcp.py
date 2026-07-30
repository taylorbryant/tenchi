"""Application MCP adapts authenticated ToolRunner calls without weakening them."""

# pyright: strict, reportUnnecessaryTypeIgnoreComment=true

import asyncio
import json
import os
import re
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import pytest
from httpx import ASGITransport, AsyncClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent
from pydantic import BaseModel, Field, PlainSerializer, field_validator
from pydantic_core import core_schema

from tenchi.errors import AppError, ConfigurationError, ErrorDef
from tenchi.mcp import (
    TOOL_MCP_PROTOCOL_VERSION,
    McpRequest,
    create_tool_mcp_server,
)
from tenchi.tools import (
    Tool,
    ToolGroup,
    ToolRunner,
    create_tool_runner,
    tool,
    tool_group,
    tool_handler,
)

VISIBLE = ErrorDef(code="VISIBLE", status=409, message="Visible rejection")
TOOL_MCP_SNAPSHOT_PATH = Path(__file__).with_name(
    f"tool_mcp_protocol_v{TOOL_MCP_PROTOCOL_VERSION}_snapshot.json"
)
UPDATE_TOOL_MCP_SNAPSHOT = "TENCHI_UPDATE_TOOL_MCP_SNAPSHOT"
TOOL_MCP_BASE_REF = "TENCHI_AGENT_PROTOCOL_BASE_REF"
TOOL_MCP_SNAPSHOT_NAME = re.compile(r"tool_mcp_protocol_v([1-9][0-9]*)_snapshot\.json")
REPOSITORY_ROOT = Path(__file__).parent.parent

_serialization_calls = 0
_input_validation_calls = 0
_shared_serialization: dict[str, object] = {}


def _serialize_once(value: int) -> int:
    global _serialization_calls
    _serialization_calls += 1
    return value if _serialization_calls == 1 else cast(int, "changed")


type SerializedInt = Annotated[
    int,
    PlainSerializer(_serialize_once, return_type=int),
]


def _serialize_shared(value: int) -> dict[str, int]:
    _shared_serialization.clear()
    _shared_serialization["value"] = value
    return cast(dict[str, int], _shared_serialization)


type SharedSerializedInt = Annotated[
    int,
    PlainSerializer(_serialize_shared, return_type=dict[str, int]),
]

type ReferenceLikeLiteral = Literal["#/$defs/not_a_reference"]


class SearchInput(BaseModel):
    query: str = Field(min_length=1)


class Match(BaseModel):
    value: str


class SearchResult(BaseModel):
    matches: list[Match]


class DeleteInput(BaseModel):
    item_id: str

    @field_validator("item_id")
    @classmethod
    def normalize_item_id(cls, value: str) -> str:
        global _input_validation_calls
        _input_validation_calls += 1
        return value.strip().lower()


class TreeInput(BaseModel):
    name: str
    children: "list[TreeInput]" = Field(  # pyright: ignore[reportUnknownVariableType]
        default_factory=list
    )


class DynamicReferenceInput:
    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: Any,
    ) -> core_schema.CoreSchema:
        del cls, source_type, handler
        return core_schema.str_schema()

    @classmethod
    def __get_pydantic_json_schema__(
        cls,
        schema: core_schema.CoreSchema,
        handler: Any,
    ) -> dict[str, Any]:
        del cls, schema, handler
        return {
            "$defs": {
                "allowed": {
                    "type": "string",
                    "const": "allowed",
                }
            },
            "$dynamicRef": "#/$defs/allowed",
        }


@dataclass(frozen=True, slots=True)
class Principal:
    name: str


@dataclass(frozen=True, slots=True)
class Context:
    principal: Principal
    events: list[str]


async def ping(context: Context) -> str:
    context.events.append(f"ping:{context.principal.name}")
    return "pong"


async def double(request: int, context: Context) -> int:
    context.events.append(f"double:{request}")
    return request * 2


async def search(request: SearchInput, context: Context) -> SearchResult:
    context.events.append(f"search:{request.query}")
    return SearchResult(matches=[Match(value=request.query)])


async def delete(request: DeleteInput, context: Context) -> None:
    context.events.append(f"delete:{request.item_id}")


async def inspect_tree(request: TreeInput, context: Context) -> str:
    context.events.append(f"tree:{request.name}")
    return request.name


async def serialize_once(context: Context) -> SerializedInt:
    context.events.append("serialize")
    return 7


async def serialize_shared(context: Context) -> SharedSerializedInt:
    context.events.append("serialize_shared")
    return 7


async def echo_reference_like_literal(
    request: ReferenceLikeLiteral,
    context: Context,
) -> ReferenceLikeLiteral:
    context.events.append(f"literal:{request}")
    return request


async def accept_dynamic_reference(
    request: DynamicReferenceInput,
    context: Context,
) -> str:
    context.events.append(f"dynamic:{request}")
    return str(request)


def application_tools() -> ToolGroup:
    return tool_group(
        tool_handler(
            tool(
                "system.ping",
                result=str,
                description="Ping the application.",
                read_only=True,
                open_world=False,
            ),
            ping,
        ),
        tool_handler(
            tool(
                "system.serialize_once",
                result=SerializedInt,
                description="Serialize one result exactly once.",
                read_only=True,
                open_world=False,
            ),
            serialize_once,
        ),
        tool_handler(
            tool(
                "numbers.double",
                request=int,
                result=int,
                description="Double one integer.",
                read_only=True,
                open_world=False,
            ),
            double,
        ),
        tool_handler(
            tool(
                "projects.search",
                request=SearchInput,
                result=SearchResult,
                description="Search visible projects.",
                errors=(VISIBLE,),
                read_only=True,
                open_world=False,
            ),
            search,
        ),
        tool_handler(
            tool(
                "projects.delete",
                request=DeleteInput,
                result=None,
                description="Delete one project.",
                destructive=True,
                open_world=False,
            ),
            delete,
        ),
    )


def runner_for(
    tools: ToolGroup,
    principal: Principal,
    events: list[str],
) -> ToolRunner:
    return create_tool_runner(
        tools=tools,
        context_factory=lambda: Context(principal, events),
    )


def static_principal_binding_regression() -> None:
    tools = tool_group()

    def runner(principal: Principal) -> ToolRunner:
        raise NotImplementedError(principal)

    def wrong_runner(principal: str) -> ToolRunner:
        raise NotImplementedError(principal)

    def wrong_visibility(
        principal: str,
        declaration: Tool[Any, Any],
    ) -> bool:
        raise NotImplementedError(principal, declaration)

    def wrong_approval(
        principal: str,
        declaration: Tool[Any, Any],
        input_value: object,
    ) -> bool:
        raise NotImplementedError(principal, declaration, input_value)

    create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=wrong_runner,  # pyright: ignore[reportArgumentType]
    )
    create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=runner,
        allow_tool=wrong_visibility,  # pyright: ignore[reportArgumentType]
    )
    create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=runner,
        approve=wrong_approval,  # pyright: ignore[reportArgumentType]
    )


async def test_mcp_discovers_manifest_schemas_annotations_and_authenticated_tools() -> (
    None
):
    tools = application_tools()
    events: list[str] = []
    requests: list[McpRequest] = []

    async def authenticate(request: McpRequest) -> Principal:
        requests.append(request)
        return Principal("member")

    def allow_tool(principal: Principal, declaration: Tool[Any, Any]) -> bool:
        assert principal.name == "member"
        return declaration.name != "projects.delete"

    server = create_tool_mcp_server(
        tools=tools,
        authenticate=authenticate,
        runner_factory=lambda principal: runner_for(tools, principal, events),
        allow_tool=allow_tool,
        name="Taskboard",
    )

    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()

    assert server.name == "Taskboard"
    assert requests[0].request_id is not None
    assert requests[0].transport_request is None
    assert [definition.name for definition in listed.tools] == [
        "numbers.double",
        "projects.search",
        "system.ping",
        "system.serialize_once",
    ]

    by_name = {definition.name: definition for definition in listed.tools}
    ping_definition = by_name["system.ping"]
    assert ping_definition.inputSchema == {
        "additionalProperties": False,
        "properties": {},
        "type": "object",
    }
    assert ping_definition.annotations is not None
    assert ping_definition.annotations.readOnlyHint is True
    assert ping_definition.annotations.destructiveHint is False
    assert ping_definition.annotations.idempotentHint is True
    assert ping_definition.annotations.openWorldHint is False

    scalar = by_name["numbers.double"]
    assert scalar.inputSchema["required"] == ["input"]
    assert scalar.inputSchema["properties"]["input"]["$ref"].endswith(
        "/tool_input_value"
    )

    search_definition = by_name["projects.search"]
    assert search_definition.inputSchema["properties"]["query"]["minLength"] == 1
    assert search_definition.outputSchema is not None
    assert search_definition.outputSchema["oneOf"][0]["properties"]["result"] == {
        "$ref": "#/$defs/tool_result_value"
    }
    assert "tool_result_definition_Match" in search_definition.outputSchema["$defs"]
    error_variants = search_definition.outputSchema["$defs"]["tool_error"]["oneOf"]
    application_error = next(
        variant
        for variant in error_variants
        if variant["properties"]["kind"].get("const") == "application_error"
    )
    assert application_error["properties"]["code"]["enum"] == ["VISIBLE"]


async def test_mcp_http_authentication_receives_the_transport_request() -> None:
    tools = application_tools()
    events: list[str] = []
    requests: list[McpRequest] = []

    def authenticate(request: McpRequest) -> Principal:
        requests.append(request)
        transport = request.transport_request
        if (
            transport is None
            or transport.headers.get("authorization") != "Bearer test-token"
        ):
            raise PermissionError("unauthenticated")
        return Principal("http-user")

    server = create_tool_mcp_server(
        tools=tools,
        authenticate=authenticate,
        runner_factory=lambda principal: runner_for(tools, principal, events),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["mcp.example.com"],
            allowed_origins=["https://mcp.example.com"],
        ),
    )
    app = server.streamable_http_app()
    transport = ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=transport,
            base_url="https://mcp.example.com",
            headers={"authorization": "Bearer test-token"},
        ) as client,
        streamable_http_client(
            "https://mcp.example.com/mcp",
            http_client=client,
        ) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        result = await session.call_tool("system.ping", {})

    assert listed.tools
    assert result.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": "pong",
    }
    assert len(requests) == 2
    assert all(request.request_id is not None for request in requests)
    assert all(request.transport_request is not None for request in requests)
    assert server.settings.stateless_http is True
    assert events == ["ping:http-user"]


async def test_mcp_invokes_object_scalar_and_no_input_tools() -> None:
    global _serialization_calls
    _serialization_calls = 0
    tools = application_tools()
    events: list[str] = []
    principal = Principal("alice")
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: principal,
        runner_factory=lambda caller: runner_for(tools, caller, events),
    )

    async with create_connected_server_and_client_session(server) as session:
        ping_result = await session.call_tool("system.ping", {})
        doubled = await session.call_tool("numbers.double", {"input": 4})
        searched = await session.call_tool(
            "projects.search",
            {"query": "tenchi"},
        )
        serialized_once = await session.call_tool("system.serialize_once", {})

    assert ping_result.isError is False
    assert ping_result.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": "pong",
    }
    assert doubled.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": 8,
    }
    assert searched.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": {"matches": [{"value": "tenchi"}]},
    }
    assert serialized_once.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": 7,
    }
    assert _serialization_calls == 1
    assert events == [
        "ping:alice",
        "double:4",
        "search:tenchi",
        "serialize",
    ]


async def test_mcp_detaches_serialized_results_before_context_cleanup() -> None:
    declaration = tool(
        "system.serialize_shared",
        result=SharedSerializedInt,
        description="Serialize through shared mutable state.",
        read_only=True,
        open_world=False,
    )
    tools = tool_group(tool_handler(declaration, serialize_shared))
    events: list[str] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        try:
            yield Context(Principal("alice"), events)
        finally:
            _shared_serialization["value"] = "mutated-during-cleanup"

    runner = create_tool_runner(
        tools=tools,
        context_factory=context_factory,
    )
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner,
    )

    result = await server.call_tool("system.serialize_shared", {})

    assert result == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": {"value": 7},
    }
    assert _shared_serialization == {"value": "mutated-during-cleanup"}
    assert events == ["serialize_shared"]


async def test_mcp_rewrites_only_actual_schema_references() -> None:
    declaration = tool(
        "system.echo_reference_like_literal",
        request=ReferenceLikeLiteral,
        result=ReferenceLikeLiteral,
        description="Echo a string that resembles a JSON Schema reference.",
        read_only=True,
        open_world=False,
    )
    tools = tool_group(tool_handler(declaration, echo_reference_like_literal))
    events: list[str] = []
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, events),
    )

    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
        result = await session.call_tool(
            "system.echo_reference_like_literal",
            {"input": "#/$defs/not_a_reference"},
        )

    definition = listed.tools[0]
    assert definition.inputSchema["$defs"]["tool_input_value"]["const"] == (
        "#/$defs/not_a_reference"
    )
    assert definition.outputSchema is not None
    assert definition.outputSchema["$defs"]["tool_result_value"]["const"] == (
        "#/$defs/not_a_reference"
    )
    assert result.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": "#/$defs/not_a_reference",
    }
    assert events == ["literal:#/$defs/not_a_reference"]


async def test_mcp_remaps_dynamic_definition_references() -> None:
    declaration = tool(
        "system.accept_dynamic_reference",
        request=DynamicReferenceInput,
        result=str,
        description="Accept a dynamically referenced value.",
        read_only=True,
        open_world=False,
    )
    tools = tool_group(tool_handler(declaration, accept_dynamic_reference))
    events: list[str] = []
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, events),
    )

    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
        accepted = await session.call_tool(
            "system.accept_dynamic_reference",
            {"input": "allowed"},
        )
        rejected = await session.call_tool(
            "system.accept_dynamic_reference",
            {"input": "rejected"},
        )

    schema = listed.tools[0].inputSchema
    assert schema["$defs"]["tool_input_value"]["$dynamicRef"] == (
        "#/$defs/tool_input_definition_allowed"
    )
    assert "tool_input_definition_allowed" in schema["$defs"]
    assert accepted.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": "allowed",
    }
    assert rejected.structuredContent is not None
    assert rejected.structuredContent["error"]["kind"] == "invalid_input"
    assert events == ["dynamic:allowed"]


async def test_mcp_keeps_recursive_object_inputs_direct() -> None:
    declaration = tool(
        "trees.inspect",
        request=TreeInput,
        result=str,
        description="Inspect one tree.",
        read_only=True,
        open_world=False,
    )
    tools = tool_group(tool_handler(declaration, inspect_tree))
    events: list[str] = []
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, events),
    )

    async with create_connected_server_and_client_session(server) as session:
        listed = await session.list_tools()
        result = await session.call_tool(
            "trees.inspect",
            {"name": "root", "children": [{"name": "leaf"}]},
        )

    schema = listed.tools[0].inputSchema
    assert schema["type"] == "object"
    assert schema["$ref"] == "#/$defs/TreeInput"
    assert "tool_input_value" not in schema["$defs"]
    assert schema["$defs"]["TreeInput"]["required"] == ["name"]
    assert result.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": "root",
    }
    assert events == ["tree:root"]


async def test_mcp_rechecks_visibility_and_hides_unknown_tools() -> None:
    tools = application_tools()
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("member"),
        runner_factory=lambda principal: runner_for(tools, principal, []),
        allow_tool=lambda principal, declaration: declaration.name == "system.ping",
    )

    async with create_connected_server_and_client_session(server) as session:
        hidden = await session.call_tool(
            "projects.search",
            {"query": "secret"},
        )

    assert hidden.isError is True
    assert hidden.structuredContent is None
    assert isinstance(hidden.content[0], TextContent)
    assert hidden.content[0].text == "unknown tool 'projects.search'"


async def test_mcp_denies_destructive_tools_without_per_call_approval() -> None:
    tools = application_tools()
    events: list[str] = []
    principal = Principal("alice")
    unapproved = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: principal,
        runner_factory=lambda caller: runner_for(tools, caller, events),
    )

    async with create_connected_server_and_client_session(unapproved) as session:
        required = await session.call_tool(
            "projects.delete",
            {"item_id": "project-1"},
        )

    assert required.isError is False
    assert required.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": False,
        "error": {
            "kind": "approval_required",
            "message": "This destructive tool requires explicit approval.",
        },
    }
    assert events == []

    approvals: list[tuple[str, str, object]] = []

    def approve(
        caller: Principal,
        declaration: Tool[Any, Any],
        input_value: object,
    ) -> bool:
        approvals.append((caller.name, declaration.name, input_value))
        return False

    denied = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: principal,
        runner_factory=lambda caller: runner_for(tools, caller, events),
        approve=approve,
    )
    async with create_connected_server_and_client_session(denied) as session:
        result = await session.call_tool(
            "projects.delete",
            {"item_id": "project-1"},
        )

    assert result.structuredContent is not None
    assert result.structuredContent["error"]["kind"] == "approval_denied"
    assert approvals == [("alice", "projects.delete", {"item_id": "project-1"})]
    assert events == []


async def test_mcp_runs_an_approved_destructive_tool() -> None:
    global _input_validation_calls
    _input_validation_calls = 0
    tools = application_tools()
    events: list[str] = []

    def approve(
        principal: Principal,
        declaration: Tool[Any, Any],
        input_value: object,
    ) -> bool:
        del principal, declaration
        assert isinstance(input_value, dict)
        assert input_value == {"item_id": "project-1"}
        input_value["item_id"] = "mutated-after-validation"
        return True

    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, events),
        approve=approve,
    )

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "projects.delete",
            {"item_id": " PROJECT-1 "},
        )

    assert result.structuredContent == {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "ok": True,
        "result": None,
    }
    assert _input_validation_calls == 1
    assert events == ["delete:project-1"]


async def test_mcp_returns_safe_structured_failures() -> None:
    events: list[str] = []

    async def reject(request: SearchInput, context: Context) -> SearchResult:
        details = (
            {"value": float("nan")}
            if request.query == "nonfinite"
            else {"field": "query"}
        )
        raise AppError(VISIBLE, details=details)

    async def explode(request: SearchInput, context: Context) -> SearchResult:
        raise RuntimeError("database-password")

    async def wrong(request: SearchInput, context: Context) -> SearchResult:
        return "result-secret"  # type: ignore[return-value]

    reject_tool = tool(
        "projects.reject",
        request=SearchInput,
        result=SearchResult,
        description="Reject visibly.",
        errors=(VISIBLE,),
        read_only=True,
    )
    explode_tool = tool(
        "projects.explode",
        request=SearchInput,
        result=SearchResult,
        description="Fail unexpectedly.",
        read_only=True,
    )
    wrong_tool = tool(
        "projects.wrong",
        request=SearchInput,
        result=SearchResult,
        description="Return an invalid result.",
        read_only=True,
    )
    tools = tool_group(
        tool_handler(reject_tool, reject),
        tool_handler(explode_tool, explode),
        tool_handler(wrong_tool, wrong),
    )
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, events),
    )

    async with create_connected_server_and_client_session(server) as session:
        invalid = await session.call_tool("projects.reject", {"query": ""})
        declared = await session.call_tool("projects.reject", {"query": "x"})
        nonfinite = await session.call_tool(
            "projects.reject",
            {"query": "nonfinite"},
        )
        unexpected = await session.call_tool("projects.explode", {"query": "x"})
        invalid_result = await session.call_tool(
            "projects.wrong",
            {"query": "x"},
        )

    assert invalid.structuredContent is not None
    assert invalid.structuredContent["error"] == {
        "kind": "invalid_input",
        "message": "Input does not match the tool schema.",
    }
    assert declared.structuredContent is not None
    assert declared.structuredContent["error"] == {
        "kind": "application_error",
        "code": "VISIBLE",
        "message": "Visible rejection",
        "details": {"field": "query"},
    }
    assert nonfinite.structuredContent is not None
    assert nonfinite.structuredContent["error"] == {
        "kind": "application_error",
        "code": "VISIBLE",
        "message": "Visible rejection",
    }
    assert unexpected.structuredContent is not None
    assert unexpected.structuredContent["error"] == {
        "kind": "failed",
        "message": "The tool invocation failed.",
    }
    assert invalid_result.structuredContent is not None
    assert invalid_result.structuredContent["error"] == {
        "kind": "invalid_result",
        "message": "The tool returned an invalid result.",
    }
    combined = str(
        [
            invalid.structuredContent,
            declared.structuredContent,
            nonfinite.structuredContent,
            unexpected.structuredContent,
            invalid_result.structuredContent,
        ]
    )
    assert "database-password" not in combined
    assert "result-secret" not in combined


async def test_mcp_masks_authentication_and_runner_configuration_failures() -> None:
    tools = application_tools()

    def fail_authentication(request: McpRequest) -> Principal:
        raise RuntimeError("token-secret")

    authentication_failure = create_tool_mcp_server(
        tools=tools,
        authenticate=fail_authentication,
        runner_factory=lambda principal: runner_for(tools, principal, []),
    )
    async with create_connected_server_and_client_session(
        authentication_failure
    ) as session:
        with pytest.raises(McpError, match="authentication failed") as failed:
            await session.list_tools()

    assert "token-secret" not in str(failed.value)

    def fail_visibility(
        principal: Principal,
        declaration: Tool[Any, Any],
    ) -> bool:
        del principal, declaration
        raise RuntimeError("visibility-secret")

    visibility_failure = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, []),
        allow_tool=fail_visibility,
    )
    async with create_connected_server_and_client_session(
        visibility_failure
    ) as session:
        with pytest.raises(McpError, match="tool visibility check failed") as failed:
            await session.list_tools()

    assert "visibility-secret" not in str(failed.value)

    def fail_approval(
        principal: Principal,
        declaration: Tool[Any, Any],
        input_value: object,
    ) -> bool:
        del principal, declaration, input_value
        raise RuntimeError("approval-secret")

    approval_failure = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(tools, principal, []),
        approve=fail_approval,
    )
    async with create_connected_server_and_client_session(approval_failure) as session:
        approval = await session.call_tool(
            "projects.delete",
            {"item_id": "project-1"},
        )

    assert approval.isError is True
    assert isinstance(approval.content[0], TextContent)
    assert approval.content[0].text == "tool approval failed"
    assert "approval-secret" not in approval.content[0].text

    other_tools = tool_group()
    bad_runner = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(other_tools, principal, []),
    )
    async with create_connected_server_and_client_session(bad_runner) as session:
        misconfigured = await session.call_tool("system.ping", {})

    assert misconfigured.isError is True
    assert isinstance(misconfigured.content[0], TextContent)
    assert misconfigured.content[0].text == "tool runner is misconfigured"

    approvals: list[str] = []

    def consume_approval(
        principal: Principal,
        declaration: Tool[Any, Any],
        input_value: object,
    ) -> bool:
        del principal, input_value
        approvals.append(declaration.name)
        return True

    bad_destructive_runner = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner_for(other_tools, principal, []),
        approve=consume_approval,
    )
    async with create_connected_server_and_client_session(
        bad_destructive_runner
    ) as session:
        misconfigured = await session.call_tool(
            "projects.delete",
            {"item_id": "project-1"},
        )

    assert misconfigured.isError is True
    assert isinstance(misconfigured.content[0], TextContent)
    assert misconfigured.content[0].text == "tool runner is misconfigured"
    assert approvals == []


async def test_mcp_cancellation_reaches_tool_cleanup() -> None:
    events: list[str] = []
    started = asyncio.Event()

    async def wait(context: Context) -> None:
        context.events.append("run")
        started.set()
        await asyncio.Event().wait()

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(Principal("alice"), events)
        except BaseException:
            events.append("context:rollback")
            raise

    wait_tool = tool(
        "system.wait",
        result=None,
        description="Wait until cancelled.",
        read_only=True,
    )
    tools = tool_group(tool_handler(wait_tool, wait))
    runner = create_tool_runner(tools=tools, context_factory=context_factory)
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("alice"),
        runner_factory=lambda principal: runner,
    )

    pending = asyncio.create_task(server.call_tool("system.wait", {}))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert events == ["context:enter", "run", "context:rollback"]


def test_mcp_validates_composition_options() -> None:
    tools = application_tools()

    with pytest.raises(ConfigurationError, match="tools must be a ToolGroup"):
        create_tool_mcp_server(
            tools=cast(ToolGroup, object()),
            authenticate=lambda request: Principal("alice"),
            runner_factory=lambda principal: runner_for(tools, principal, []),
        )
    with pytest.raises(ConfigurationError, match="authenticate must accept 1"):
        create_tool_mcp_server(
            tools=tools,
            authenticate=cast(Any, lambda: Principal("alice")),
            runner_factory=lambda principal: runner_for(tools, principal, []),
        )
    with pytest.raises(ConfigurationError, match="name must be a non-empty"):
        create_tool_mcp_server(
            tools=tools,
            authenticate=lambda request: Principal("alice"),
            runner_factory=lambda principal: runner_for(tools, principal, []),
            name=" ",
        )
    with pytest.raises(
        ConfigurationError,
        match="transport_security must be a TransportSecuritySettings",
    ):
        create_tool_mcp_server(
            tools=tools,
            authenticate=lambda request: Principal("alice"),
            runner_factory=lambda principal: runner_for(tools, principal, []),
            transport_security=cast(Any, object()),
        )


async def _render_tool_mcp_protocol() -> dict[str, object]:
    tools = application_tools()
    server = create_tool_mcp_server(
        tools=tools,
        authenticate=lambda request: Principal("snapshot"),
        runner_factory=lambda principal: runner_for(tools, principal, []),
    )
    definitions = [
        definition.model_dump(mode="json", by_alias=True, exclude_none=True)
        for definition in await server.list_tools()
    ]
    return {
        "schema_version": TOOL_MCP_PROTOCOL_VERSION,
        "tools": definitions,
    }


def _tool_mcp_snapshot_version(path: Path) -> int | None:
    matched = TOOL_MCP_SNAPSHOT_NAME.fullmatch(path.name)
    return int(matched.group(1)) if matched is not None else None


def _versioned_tool_mcp_snapshot_paths() -> dict[int, Path]:
    snapshots: dict[int, Path] = {}
    for path in Path(__file__).parent.glob("tool_mcp_protocol_v*_snapshot.json"):
        version = _tool_mcp_snapshot_version(path)
        assert version is not None, f"Invalid application MCP snapshot: {path.name}"
        assert version not in snapshots
        snapshots[version] = path
    return snapshots


def _git_tool_mcp_snapshot_texts(base_ref: str) -> dict[int, str]:
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_ref, "--", "tests"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, (
        f"Could not inspect application MCP snapshots at {base_ref!r}: "
        f"{listed.stderr.strip()}"
    )

    snapshots: dict[int, str] = {}
    for relative in listed.stdout.splitlines():
        path = Path(relative)
        version = _tool_mcp_snapshot_version(path)
        if path.parent != Path("tests") or version is None:
            continue
        shown = subprocess.run(
            ["git", "show", f"{base_ref}:{relative}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert shown.returncode == 0, (
            f"Could not read {relative} at {base_ref!r}: {shown.stderr.strip()}"
        )
        snapshots[version] = shown.stdout
    return snapshots


async def test_tool_mcp_protocol_matches_its_versioned_snapshot() -> None:
    current = await _render_tool_mcp_protocol()
    rendered = json.dumps(current, indent=2, sort_keys=True) + "\n"

    if os.environ.get(UPDATE_TOOL_MCP_SNAPSHOT):
        if TOOL_MCP_SNAPSHOT_PATH.exists():
            assert TOOL_MCP_SNAPSHOT_PATH.read_text(encoding="utf-8") == rendered, (
                "Application MCP schemas changed; bump "
                "TOOL_MCP_PROTOCOL_VERSION and create a new snapshot"
            )
        else:
            TOOL_MCP_SNAPSHOT_PATH.write_text(rendered, encoding="utf-8")

    assert TOOL_MCP_SNAPSHOT_PATH.exists(), (
        f"No application MCP v{TOOL_MCP_PROTOCOL_VERSION} snapshot found. "
        f"Generate one with {UPDATE_TOOL_MCP_SNAPSHOT}=1 uv run pytest "
        "tests/test_tool_mcp.py"
    )
    assert TOOL_MCP_SNAPSHOT_PATH.read_text(encoding="utf-8") == rendered, (
        "Application MCP schemas changed without a protocol version bump"
    )


def test_tool_mcp_snapshot_history_is_complete() -> None:
    assert set(_versioned_tool_mcp_snapshot_paths()) == set(
        range(1, TOOL_MCP_PROTOCOL_VERSION + 1)
    )


async def test_tool_mcp_protocol_matches_its_git_baseline() -> None:
    base_ref = os.environ.get(TOOL_MCP_BASE_REF)
    if base_ref is None:
        return

    baseline_snapshots = _git_tool_mcp_snapshot_texts(base_ref)
    if not baseline_snapshots:
        assert TOOL_MCP_PROTOCOL_VERSION == 1
        return

    previous_version = max(baseline_snapshots)
    assert set(baseline_snapshots) == set(range(1, previous_version + 1))
    assert TOOL_MCP_PROTOCOL_VERSION in (previous_version, previous_version + 1)

    local_snapshots = _versioned_tool_mcp_snapshot_paths()
    for version, baseline_text in baseline_snapshots.items():
        if version >= TOOL_MCP_PROTOCOL_VERSION:
            continue
        assert local_snapshots[version].read_text(encoding="utf-8") == baseline_text, (
            f"Historical application MCP v{version} must remain byte-for-byte stable"
        )

    if previous_version == TOOL_MCP_PROTOCOL_VERSION:
        current = await _render_tool_mcp_protocol()
        rendered = json.dumps(current, indent=2, sort_keys=True) + "\n"
        assert baseline_snapshots[TOOL_MCP_PROTOCOL_VERSION] == rendered, (
            "Application MCP schemas changed without a protocol version bump"
        )
