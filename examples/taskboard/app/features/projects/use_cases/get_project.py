from app.server.context import AppContext
from app.shared.errors import project_not_found
from app.shared.users import require_user
from tenchi.errors import AppError

from ..schemas import GetProjectParams, Project


async def get_project(params: GetProjectParams, context: AppContext) -> Project:
    user = require_user(context.user)
    project = await context.projects.get(params.project_id)
    # Another owner's project is reported as absent, not as forbidden, so
    # project ids cannot be probed.
    if project is None or project.owner_id != user.id:
        raise AppError(project_not_found, details={"project_id": params.project_id})
    return project
