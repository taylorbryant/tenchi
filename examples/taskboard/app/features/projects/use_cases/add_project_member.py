from app.server.context import AppContext
from app.shared.users import require_user

from ..policy import ensure_can_write_project
from ..schemas import AddProjectMember, GetProjectParams, Project


async def add_project_member(
    params: GetProjectParams, request: AddProjectMember, context: AppContext
) -> Project:
    user = require_user(context.user)

    project = await context.projects.get(params.project_id)
    project = ensure_can_write_project(user, project, project_id=params.project_id)

    if request.user_id in project.member_ids or request.user_id == project.owner_id:
        return project
    updated = project.model_copy(
        update={"member_ids": (*project.member_ids, request.user_id)}
    )
    return await context.projects.save(updated)
