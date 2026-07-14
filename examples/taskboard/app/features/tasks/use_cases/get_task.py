from app.features.projects.policy import can_view_project
from app.server.context import AppContext
from app.shared.errors import task_not_found
from app.shared.users import require_user
from tenchi.errors import AppError

from ..schemas import GetTaskParams, Task


async def get_task(params: GetTaskParams, context: AppContext) -> Task:
    user = require_user(context.user)

    task = await context.tasks.get(params.task_id)
    if task is None:
        raise AppError(task_not_found, details={"task_id": params.task_id})

    project = await context.projects.get(task.project_id)
    # Another owner's task is reported as absent, not as forbidden, so task
    # ids cannot be probed.
    if not can_view_project(user, project):
        raise AppError(task_not_found, details={"task_id": params.task_id})

    return task
