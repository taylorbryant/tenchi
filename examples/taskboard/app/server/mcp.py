"""Application MCP composition for one already-authenticated user."""

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from app.shared.users import User
from tenchi.mcp import McpRequest, create_tool_mcp_server
from tenchi.tools import Tool

from .tools import create_user_tool_runner, tools

ToolApproval = Callable[
    [User, Tool[Any, Any], object],
    bool | Awaitable[bool],
]


def create_user_mcp_server(
    *,
    database_path: str,
    user: User,
    approve: ToolApproval | None = None,
) -> MCPServer[dict[str, Any]]:
    """Expose only tools whose runner carries the authenticated ``user``."""

    def authenticate(request: McpRequest) -> User:
        del request
        return user

    return create_tool_mcp_server(
        tools=tools,
        authenticate=authenticate,
        runner_factory=lambda principal: create_user_tool_runner(
            database_path=database_path,
            user=principal,
        ),
        approve=approve,
        name="Taskboard",
        instructions=(
            "Search visible projects freely. Move tasks only after the caller "
            "has approved the exact destructive operation."
        ),
    )
