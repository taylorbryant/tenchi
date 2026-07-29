from app.shared.errors import unauthorized
from tenchi.tools import tool, tool_group, tool_handler

from .schemas import Project
from .use_cases.list_projects import list_projects

search_projects_tool = tool(
    "projects.search",
    result=list[Project],
    description="List projects owned by the authenticated user.",
    errors=(unauthorized,),
    read_only=True,
    open_world=False,
)

tools = tool_group(
    tool_handler(search_projects_tool, list_projects),
)
