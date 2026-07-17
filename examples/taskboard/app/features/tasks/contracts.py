from pydantic import BaseModel, Field

from app.shared.errors import (
    forbidden,
    idempotency_conflict,
    precondition_failed,
    precondition_required,
    project_not_found,
    task_not_found,
)
from tenchi.contracts import contract
from tenchi.pagination import Page

from .schemas import (
    TASK_ETAG_PATTERN,
    CreateTask,
    CreateTaskHeaders,
    GetTaskParams,
    ListTasksQuery,
    Task,
    UpdateTask,
    UpdateTaskHeaders,
)


class TaskVersionHeaders(BaseModel):
    etag: str = Field(alias="ETag", pattern=TASK_ETAG_PATTERN)


class CreatedTaskHeaders(TaskVersionHeaders):
    location: str = Field(alias="Location")


create_task_contract = contract(
    method="POST",
    path="/tasks",
    request=CreateTask,
    headers=CreateTaskHeaders,
    response=Task,
    response_headers=CreatedTaskHeaders,
    status=201,
    timeout=10.0,
    errors=(project_not_found, forbidden, idempotency_conflict),
    summary="Create a task in one of the current user's projects",
    description=(
        "Idempotency-Key is required. Retrying the same validated input with "
        "the same key returns the original task response; reusing a key for "
        "different input returns 409."
    ),
    tags=("tasks",),
)

get_task_contract = contract(
    method="GET",
    path="/tasks/{task_id}",
    params=GetTaskParams,
    response=Task,
    response_headers=TaskVersionHeaders,
    errors=(task_not_found,),
    summary="Get one of the current user's tasks",
    tags=("tasks",),
)

list_tasks_contract = contract(
    method="GET",
    path="/tasks",
    query=ListTasksQuery,
    response=Page[Task],
    summary="List tasks the current user can view, filtered and paginated",
    tags=("tasks",),
)

update_task_contract = contract(
    method="PATCH",
    path="/tasks/{task_id}",
    params=GetTaskParams,
    headers=UpdateTaskHeaders,
    request=UpdateTask,
    response=Task,
    response_headers=TaskVersionHeaders,
    errors=(
        task_not_found,
        forbidden,
        precondition_required,
        precondition_failed,
    ),
    summary="Partially update one of the current user's tasks",
    description=(
        "Requires the strong ETag from the latest task response in If-Match. "
        "A missing precondition returns 428; a stale one returns 412."
    ),
    tags=("tasks",),
)
