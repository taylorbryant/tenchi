from tenchi.routes import route, route_group

from .contracts import create_todo_contract, get_todo_contract, list_todos_contract
from .use_cases.create_todo import create_todo
from .use_cases.get_todo import get_todo
from .use_cases.list_todos import list_todos

routes = route_group(
    route(create_todo_contract, create_todo),
    route(list_todos_contract, list_todos),
    route(get_todo_contract, get_todo),
)
