from pydantic import BaseModel, Field
from tenchi.contracts import contract
from tenchi.idempotency import IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_PROGRESS

from .schemas import CreateTodo, CreateTodoHeaders, Todo


class CreatedTodoHeaders(BaseModel):
    location: str = Field(alias="Location")


create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    headers=CreateTodoHeaders,
    response=Todo,
    response_headers=CreatedTodoHeaders,
    status=201,
    request_examples={"create": CreateTodo(title="Buy milk")},
    response_examples={
        "created": Todo(id="todo_123", title="Buy milk", completed=False)
    },
    errors=(IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_PROGRESS),
)

list_todos_contract = contract(
    method="GET",
    path="/todos",
    response=list[Todo],
)
