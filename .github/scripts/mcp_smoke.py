"""Connect to a generated app's installed Tenchi MCP server over stdio."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client

from tenchi import __version__
from tenchi._cli_results import AGENT_PROTOCOL_VERSION


async def main() -> None:
    root = Path(sys.argv[1]).resolve()
    parameters = StdioServerParameters(
        command="uv",
        args=["run", "tenchi", "mcp", "--root", str(root)],
        cwd=root,
    )
    async with Client(stdio_client(parameters)) as client:
        server_info = client.server_info
        tools = await client.list_tools()
        routes = await client.call_tool("routes", {})
        application_tools = await client.call_tool("tools", {})
        tools_diff = await client.call_tool("tools_diff", {})
        evaluation_diff = await client.call_tool("evaluation_diff", {})
        preflight = await client.call_tool("preflight", {})
        evaluations = await client.call_tool("evaluation_list", {})
        tasks = await client.call_tool("task_list", {})
    names = {tool.name for tool in tools.tools}
    expected = {
        "app_map",
        "routes",
        "tools",
        "doctor",
        "preflight",
        "evaluation_list",
        "task_list",
        "openapi_diff",
        "tools_diff",
        "evaluation_diff",
        "make_preview",
        "verify",
        "check",
    }
    if names != expected:
        raise RuntimeError(f"unexpected MCP tools: {sorted(names)}")
    if server_info is None or server_info.name != "Tenchi":
        raise RuntimeError("MCP server reported the wrong name")
    if server_info.version != __version__:
        raise RuntimeError("MCP server reported the wrong Tenchi version")
    if routes.is_error or routes.structured_content is None:
        raise RuntimeError("routes MCP smoke call failed")
    if routes.structured_content.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("routes MCP result is not versioned")
    if application_tools.is_error or application_tools.structured_content is None:
        raise RuntimeError("tools MCP smoke call failed")
    if (
        application_tools.structured_content.get("schema_version")
        != AGENT_PROTOCOL_VERSION
    ):
        raise RuntimeError("tools MCP result is not versioned")
    if application_tools.structured_content.get("manifest", {}).get("tools") != []:
        raise RuntimeError("generated app unexpectedly registered application tools")
    if tools_diff.is_error or tools_diff.structured_content is None:
        raise RuntimeError("tools_diff MCP smoke call failed")
    if tools_diff.structured_content.get("compatible") is not True:
        raise RuntimeError("generated app tool snapshot is incompatible")
    if evaluation_diff.is_error or evaluation_diff.structured_content is None:
        raise RuntimeError("evaluation_diff MCP smoke call failed")
    if evaluation_diff.structured_content.get("compatible") is not True:
        raise RuntimeError("generated app evaluation snapshot is incompatible")
    if tasks.is_error or tasks.structured_content is None:
        raise RuntimeError("task_list MCP smoke call failed")
    if tasks.structured_content.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("task_list MCP result is not versioned")
    if tasks.structured_content.get("tasks") != []:
        raise RuntimeError("generated app unexpectedly registered operational tasks")
    if preflight.is_error or preflight.structured_content is None:
        raise RuntimeError("preflight MCP smoke call failed")
    if preflight.structured_content.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("preflight MCP result is not versioned")
    if preflight.structured_content.get("checks") != []:
        raise RuntimeError("generated app unexpectedly registered preflight checks")
    if evaluations.is_error or evaluations.structured_content is None:
        raise RuntimeError("evaluation_list MCP smoke call failed")
    if evaluations.structured_content.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("evaluation_list MCP result is not versioned")
    if evaluations.structured_content.get("evaluations") != []:
        raise RuntimeError("generated app unexpectedly registered evaluations")


if __name__ == "__main__":
    asyncio.run(main())
