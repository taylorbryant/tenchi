from tenchi.tools import tool, tool_group, tool_handler

from .schemas import Todo
from .use_cases.list_todos import list_todos

list_todos_tool = tool(
    "todos.list",
    request=None,
    result=list[Todo],
    description="List persisted todos",
    read_only=True,
    idempotent=True,
)

tools = tool_group(tool_handler(list_todos_tool, list_todos))
