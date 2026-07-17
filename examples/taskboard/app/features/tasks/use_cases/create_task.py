import hashlib
import json

from app.features.projects.policy import ensure_can_write_project
from app.server.context import AppContext
from app.shared.errors import idempotency_conflict
from app.shared.users import OwnerScope, require_user
from tenchi.errors import AppError

from ..schemas import CreateTask, CreateTaskHeaders, Task


async def create_task(
    headers: CreateTaskHeaders,
    request: CreateTask,
    context: AppContext,
) -> Task:
    user = require_user(context.user)

    project = await context.projects.get(request.project_id)
    ensure_can_write_project(user, project, project_id=request.project_id)

    task = await context.tasks.create_idempotent(
        project_id=request.project_id,
        title=request.title,
        owner=OwnerScope(owner_id=user.id),
        idempotency_key=headers.idempotency_key,
        request_fingerprint=_request_fingerprint(request),
    )
    if task is None:
        raise AppError(
            idempotency_conflict,
            details={"idempotency_key": headers.idempotency_key},
        )
    return task


def _request_fingerprint(request: CreateTask) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
