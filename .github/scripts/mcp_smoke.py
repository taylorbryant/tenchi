"""Connect to a generated app's installed Tenchi MCP server over stdio."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tenchi import __version__


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
        tasks = await session.call_tool("task_list", {})
    names = {tool.name for tool in tools.tools}
    expected = {
        "app_map",
        "routes",
        "doctor",
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
    if routes.structuredContent.get("schema_version") != 2:
        raise RuntimeError("routes MCP result is not versioned")
    if tasks.isError or tasks.structuredContent is None:
        raise RuntimeError("task_list MCP smoke call failed")
    if tasks.structuredContent.get("schema_version") != 2:
        raise RuntimeError("task_list MCP result is not versioned")
    if tasks.structuredContent.get("tasks") != []:
        raise RuntimeError("generated app unexpectedly registered operational tasks")


if __name__ == "__main__":
    asyncio.run(main())
