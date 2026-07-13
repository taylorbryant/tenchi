from pydantic import BaseModel, Field


class CreateTodo(BaseModel):
    title: str = Field(min_length=1)


class GetTodoParams(BaseModel):
    todo_id: str


class Todo(BaseModel):
    id: str
    title: str
    completed: bool
