from app.server.context import AppContext
from app.shared.errors import task_not_found
from app.shared.users import require_user
from tenchi.errors import AppError

from ..schemas import GetTaskParams, Task, UpdateTask


async def update_task(
    params: GetTaskParams, request: UpdateTask, context: AppContext
) -> Task:
    user = require_user(context.user)

    task = await context.tasks.get(params.task_id)
    if task is None:
        raise AppError(task_not_found, details={"task_id": params.task_id})

    project = await context.projects.get(task.project_id)
    if project is None or project.owner_id != user.id:
        raise AppError(task_not_found, details={"task_id": params.task_id})

    changes = request.model_dump(exclude_none=True)
    if not changes:
        return task
    return await context.tasks.save(task.model_copy(update=changes))
