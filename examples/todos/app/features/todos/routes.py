from tenchi.routes import route, route_group

from .contracts import (
    CreatedTodoHeaders,
    create_todo_contract,
    get_todo_contract,
    list_todos_contract,
)
from .schemas import Todo
from .use_cases.create_todo import create_todo
from .use_cases.get_todo import get_todo
from .use_cases.list_todos import list_todos


def create_todo_headers(todo: Todo) -> CreatedTodoHeaders:
    return CreatedTodoHeaders(Location=f"/todos/{todo.id}")


routes = route_group(
    route(
        create_todo_contract,
        create_todo,
        response_headers=create_todo_headers,
    ),
    route(list_todos_contract, list_todos),
    route(get_todo_contract, get_todo),
)
