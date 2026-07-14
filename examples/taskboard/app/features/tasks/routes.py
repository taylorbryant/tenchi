from tenchi.routes import route, route_group

from .contracts import (
    create_task_contract,
    get_task_contract,
    list_tasks_contract,
    update_task_contract,
)
from .use_cases.create_task import create_task
from .use_cases.get_task import get_task
from .use_cases.list_tasks import list_tasks
from .use_cases.update_task import update_task

routes = route_group(
    route(create_task_contract, create_task),
    route(get_task_contract, get_task),
    route(list_tasks_contract, list_tasks),
    route(update_task_contract, update_task),
)
