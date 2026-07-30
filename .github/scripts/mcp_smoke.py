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
        preflight = await session.call_tool("preflight", {})
        tasks = await session.call_tool("task_list", {})
    names = {tool.name for tool in tools.tools}
    expected = {
        "app_map",
        "routes",
        "doctor",
        "preflight",
        "task_list",
        "openapi_diff",
        "make_preview",
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


if __name__ == "__main__":
    asyncio.run(main())
