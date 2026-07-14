from app.shared.errors import forbidden, project_not_found, task_not_found
from tenchi.contracts import contract

from .schemas import (
    CreateTask,
    GetTaskParams,
    ListTasksQuery,
    Task,
    TaskPage,
    UpdateTask,
)

create_task_contract = contract(
    method="POST",
    path="/tasks",
    request=CreateTask,
    response=Task,
    status=201,
    errors=(project_not_found, forbidden),
    summary="Create a task in one of the current user's projects",
    tags=("tasks",),
)

get_task_contract = contract(
    method="GET",
    path="/tasks/{task_id}",
    params=GetTaskParams,
    response=Task,
    errors=(task_not_found,),
    summary="Get one of the current user's tasks",
    tags=("tasks",),
)

list_tasks_contract = contract(
    method="GET",
    path="/tasks",
    query=ListTasksQuery,
    response=TaskPage,
    summary="List the current user's tasks, filtered and paginated",
    tags=("tasks",),
)

update_task_contract = contract(
    method="PATCH",
    path="/tasks/{task_id}",
    params=GetTaskParams,
    request=UpdateTask,
    response=Task,
    errors=(task_not_found,),
    summary="Partially update one of the current user's tasks",
    tags=("tasks",),
)
