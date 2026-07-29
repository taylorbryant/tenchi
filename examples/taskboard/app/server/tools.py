"""Authenticated application-tool composition without a transport dependency."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace

from app.features.projects.tools import tools as project_tools
from app.features.tasks.tools import tools as task_tools
from app.server.context import AppContext
from app.server.observability import use_case_observers
from app.server.runtime import create_context, create_lifespan
from app.shared.users import User
from tenchi.tools import ToolRunner, create_tool_runner, tool_group

tools = tool_group(project_tools, task_tools)


def create_user_tool_runner(
    *,
    database_path: str,
    user: User,
) -> ToolRunner:
    """Create a runner after a future transport has authenticated ``user``."""

    @asynccontextmanager
    async def tool_context(state: str) -> AsyncGenerator[AppContext]:
        async with create_context(state) as context:
            yield replace(context, user=user)

    return create_tool_runner(
        tools=tools,
        context_factory=tool_context,
        lifespan=create_lifespan(database_path),
        use_case_observers=use_case_observers,
    )
