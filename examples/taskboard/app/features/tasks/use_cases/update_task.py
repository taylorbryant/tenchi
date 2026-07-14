from app.features.projects.policy import can_view_project, ensure_can_write_project
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
    # Fetch-then-ask, in two steps: what you cannot view is absent (404);
    # what you can view but do not own refuses the write (403). Updating
    # is a write, so it takes the same ability as creating.
    if not can_view_project(user, project):
        raise AppError(task_not_found, details={"task_id": params.task_id})
    ensure_can_write_project(user, project, project_id=task.project_id)

    changes = request.model_dump(exclude_none=True)
    if not changes:
        return task
    return await context.tasks.save(task.model_copy(update=changes))
