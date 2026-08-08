"""Bearer-authenticated application MCP composition for Fieldnotes."""

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from tenchi.mcp import McpRequest, create_tool_mcp_server
from tenchi.tools import Tool

from app.infra.static_token_directory import DEMO_TOKENS, StaticTokenDirectory
from app.shared.users import TokenDirectory, User

from .runtime import DATABASE_PATH
from .tools import create_user_tool_runner, tools

ToolApproval = Callable[[User, Tool[Any, Any], object], bool | Awaitable[bool]]


def create_fieldnotes_mcp_server(
    *,
    database_path: str,
    token_directory: TokenDirectory,
    approve: ToolApproval | None = None,
    transport_security: TransportSecuritySettings | None = None,
) -> MCPServer[dict[str, Any]]:
    async def authenticate(request: McpRequest) -> User:
        headers = request.headers
        if headers is None:
            raise PermissionError("Fieldnotes MCP requires HTTP authentication")
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.casefold() != "bearer" or not token:
            raise PermissionError("Fieldnotes MCP requires a bearer token")
        user = await token_directory.lookup(token)
        if user is None:
            raise PermissionError("The bearer token is invalid")
        return user

    return create_tool_mcp_server(
        tools=tools,
        authenticate=authenticate,
        runner_factory=lambda principal: create_user_tool_runner(
            database_path=database_path,
            user=principal,
        ),
        approve=approve,
        transport_security=transport_security,
        name="Fieldnotes",
        instructions=(
            "Search and answer from visible evidence. Saving new material "
            "requires approval for the exact tool call."
        ),
    )


mcp = create_fieldnotes_mcp_server(
    database_path=DATABASE_PATH,
    token_directory=StaticTokenDirectory(DEMO_TOKENS),
)
app = mcp.streamable_http_app()
