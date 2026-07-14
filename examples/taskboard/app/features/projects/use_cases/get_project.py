from app.server.context import AppContext
from app.shared.users import require_user

from ..policy import ensure_can_view_project
from ..schemas import GetProjectParams, Project


async def get_project(params: GetProjectParams, context: AppContext) -> Project:
    user = require_user(context.user)
    project = await context.projects.get(params.project_id)
    return ensure_can_view_project(user, project, project_id=params.project_id)
