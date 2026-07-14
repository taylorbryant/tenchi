from app.server.context import AppContext
from app.shared.errors import forbidden, project_not_found
from app.shared.users import require_user
from tenchi.errors import AppError

from ..schemas import CreateTask, Task


async def create_task(request: CreateTask, context: AppContext) -> Task:
    user = require_user(context.user)

    project = await context.projects.get(request.project_id)
    if project is None:
        raise AppError(project_not_found, details={"project_id": request.project_id})
    if project.owner_id != user.id:
        raise AppError(forbidden, details={"project_id": request.project_id})

    return await context.tasks.create(project_id=project.id, title=request.title)
