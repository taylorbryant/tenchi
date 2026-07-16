from pydantic import BaseModel, Field

from app.shared.errors import todo_not_found
from tenchi.contracts import contract

from .schemas import CreateTodo, GetTodoParams, ListTodosQuery, Todo


class CreatedTodoHeaders(BaseModel):
    location: str = Field(alias="Location")


create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    response_headers=CreatedTodoHeaders,
    status=201,
    summary="Create a todo",
    tags=("todos",),
)

list_todos_contract = contract(
    method="GET",
    path="/todos",
    query=ListTodosQuery,
    response=list[Todo],
    summary="List todos",
    description="Returns all todos, optionally filtered by completion state.",
    tags=("todos",),
)

get_todo_contract = contract(
    method="GET",
    path="/todos/{todo_id}",
    params=GetTodoParams,
    response=Todo,
    errors=(todo_not_found,),
    summary="Get a todo by id",
    tags=("todos",),
)
