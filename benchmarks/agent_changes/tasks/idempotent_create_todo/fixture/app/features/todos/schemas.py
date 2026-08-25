from pydantic import BaseModel, Field


class CreateTodo(BaseModel):
    title: str = Field(min_length=1)


class CreateTodoHeaders(BaseModel):
    """The published retry key; durable enforcement is the benchmark task."""

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class Todo(BaseModel):
    id: str
    title: str
    completed: bool
