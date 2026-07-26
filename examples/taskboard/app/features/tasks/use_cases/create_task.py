from app.features.projects.policy import ensure_can_write_project
from app.server.context import AppContext
from app.shared.users import require_user
from tenchi.idempotency import fingerprint, run_idempotently

from ..schemas import CreateTask, CreateTaskHeaders, Task


async def create_task(
    headers: CreateTaskHeaders,
    request: CreateTask,
    context: AppContext,
) -> Task:
    user = require_user(context.user)

    project = await context.projects.get(request.project_id)
    ensure_can_write_project(user, project, project_id=request.project_id)

    async def create() -> Task:
        return await context.tasks.create(
            project_id=request.project_id,
            title=request.title,
        )

    return await run_idempotently(
        context.idempotency,
        namespace="tasks.create",
        scope=user.id,
        key=headers.idempotency_key,
        fingerprint=fingerprint(request, annotation=CreateTask),
        result_type=Task,
        operation=create,
    )
