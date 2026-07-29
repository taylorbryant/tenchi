from app.features.projects.policy import can_view_project, ensure_can_write_project
from app.server.context import AppContext
from app.shared.errors import precondition_failed, task_not_found
from app.shared.users import require_user
from tenchi.errors import AppError
from tenchi.idempotency import fingerprint, run_idempotently
from tenchi.rate_limits import enforce_rate_limit

from ..schemas import MoveTaskInput, Task


async def move_task(request: MoveTaskInput, context: AppContext) -> Task:
    """Move a task once for an authenticated user.

    The rate-limit consume lives inside the idempotent operation, so a replay
    of one logical tool call returns its stored result without spending quota
    twice.
    """
    user = require_user(context.user)

    async def move() -> Task:
        await enforce_rate_limit(
            context.rate_limits,
            namespace="tools.tasks.move",
            scope=user.id,
            limit=5,
            window_seconds=60,
        )
        task = await context.tasks.get(request.task_id)
        if task is None:
            raise AppError(task_not_found, details={"task_id": request.task_id})

        project = await context.projects.get(task.project_id)
        if not can_view_project(user, project):
            raise AppError(task_not_found, details={"task_id": request.task_id})
        ensure_can_write_project(user, project, project_id=task.project_id)

        if task.version != request.expected_version:
            raise AppError(
                precondition_failed,
                details={
                    "task_id": task.id,
                    "expected_version": request.expected_version,
                },
            )
        if task.status == request.status:
            return task

        updated = await context.tasks.save(
            task.model_copy(update={"status": request.status}),
            expected_version=request.expected_version,
        )
        if updated is None:
            raise AppError(
                precondition_failed,
                details={
                    "task_id": task.id,
                    "expected_version": request.expected_version,
                },
            )
        return updated

    return await run_idempotently(
        context.idempotency,
        namespace="tools.tasks.move",
        scope=user.id,
        key=request.idempotency_key,
        fingerprint=fingerprint(request, annotation=MoveTaskInput),
        result_type=Task,
        operation=move,
    )
