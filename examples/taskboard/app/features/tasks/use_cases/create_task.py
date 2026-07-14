from app.features.projects.policy import ensure_can_write_project
from app.server.context import AppContext
from app.shared.users import require_user

from ..schemas import CreateTask, Task


async def create_task(request: CreateTask, context: AppContext) -> Task:
    user = require_user(context.user)

    project = await context.projects.get(request.project_id)
    ensure_can_write_project(user, project, project_id=request.project_id)

    return await context.tasks.create(
        project_id=request.project_id, title=request.title
    )
