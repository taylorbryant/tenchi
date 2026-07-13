from app.shared.errors import todo_not_found
from tenchi.contracts import contract

from .schemas import CreateTodo, GetTodoParams, Todo

create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    status=201,
)

list_todos_contract = contract(
    method="GET",
    path="/todos",
    response=list[Todo],
)

get_todo_contract = contract(
    method="GET",
    path="/todos/{todo_id}",
    params=GetTodoParams,
    response=Todo,
    errors=(todo_not_found,),
)
