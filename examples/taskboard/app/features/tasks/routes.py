from tenchi.routes import route, route_group

from .contracts import (
    CreatedTaskHeaders,
    TaskVersionHeaders,
    create_task_contract,
    get_task_contract,
    list_tasks_contract,
    update_task_contract,
)
from .schemas import Task
from .use_cases.create_task import create_task
from .use_cases.get_task import get_task
from .use_cases.list_tasks import list_tasks
from .use_cases.update_task import update_task


def task_version_headers(task: Task) -> TaskVersionHeaders:
    return TaskVersionHeaders.model_validate({"ETag": f'"{task.version}"'})


def created_task_headers(task: Task) -> CreatedTaskHeaders:
    return CreatedTaskHeaders.model_validate(
        {
            "ETag": f'"{task.version}"',
            "Location": f"/tasks/{task.id}",
        }
    )


routes = route_group(
    route(
        create_task_contract,
        create_task,
        response_headers=created_task_headers,
    ),
    route(
        get_task_contract,
        get_task,
        response_headers=task_version_headers,
    ),
    route(list_tasks_contract, list_tasks),
    route(
        update_task_contract,
        update_task,
        response_headers=task_version_headers,
    ),
)
