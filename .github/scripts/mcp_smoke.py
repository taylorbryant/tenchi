"""Connect to a generated app's installed Tenchi MCP server over stdio."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
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
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        routes = await session.call_tool("routes", {})
        application_tools = await session.call_tool("tools", {})
        tools_diff = await session.call_tool("tools_diff", {})
        evaluation_diff = await session.call_tool("evaluation_diff", {})
        preflight = await session.call_tool("preflight", {})
        evaluations = await session.call_tool("evaluation_list", {})
        tasks = await session.call_tool("task_list", {})
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
    if initialized.serverInfo.name != "Tenchi":
        raise RuntimeError("MCP server reported the wrong name")
    if initialized.serverInfo.version != __version__:
        raise RuntimeError("MCP server reported the wrong Tenchi version")
    if routes.isError or routes.structuredContent is None:
        raise RuntimeError("routes MCP smoke call failed")
    if routes.structuredContent.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("routes MCP result is not versioned")
    if application_tools.isError or application_tools.structuredContent is None:
        raise RuntimeError("tools MCP smoke call failed")
    if (
        application_tools.structuredContent.get("schema_version")
        != AGENT_PROTOCOL_VERSION
    ):
        raise RuntimeError("tools MCP result is not versioned")
    if application_tools.structuredContent.get("manifest", {}).get("tools") != []:
        raise RuntimeError("generated app unexpectedly registered application tools")
    if tools_diff.isError or tools_diff.structuredContent is None:
        raise RuntimeError("tools_diff MCP smoke call failed")
    if tools_diff.structuredContent.get("compatible") is not True:
        raise RuntimeError("generated app tool snapshot is incompatible")
    if evaluation_diff.isError or evaluation_diff.structuredContent is None:
        raise RuntimeError("evaluation_diff MCP smoke call failed")
    if evaluation_diff.structuredContent.get("compatible") is not True:
        raise RuntimeError("generated app evaluation snapshot is incompatible")
    if tasks.isError or tasks.structuredContent is None:
        raise RuntimeError("task_list MCP smoke call failed")
    if tasks.structuredContent.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("task_list MCP result is not versioned")
    if tasks.structuredContent.get("tasks") != []:
        raise RuntimeError("generated app unexpectedly registered operational tasks")
    if preflight.isError or preflight.structuredContent is None:
        raise RuntimeError("preflight MCP smoke call failed")
    if preflight.structuredContent.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("preflight MCP result is not versioned")
    if preflight.structuredContent.get("checks") != []:
        raise RuntimeError("generated app unexpectedly registered preflight checks")
    if evaluations.isError or evaluations.structuredContent is None:
        raise RuntimeError("evaluation_list MCP smoke call failed")
    if evaluations.structuredContent.get("schema_version") != AGENT_PROTOCOL_VERSION:
        raise RuntimeError("evaluation_list MCP result is not versioned")
    if evaluations.structuredContent.get("evaluations") != []:
        raise RuntimeError("generated app unexpectedly registered evaluations")


if __name__ == "__main__":
    asyncio.run(main())
