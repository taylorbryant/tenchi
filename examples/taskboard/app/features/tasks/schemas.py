from enum import StrEnum

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    TODO = "todo"
    DOING = "doing"
    DONE = "done"


class CreateTask(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=200)


class UpdateTask(BaseModel):
    """Partial update: ``None`` fields are left unchanged."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: TaskStatus | None = None


class GetTaskParams(BaseModel):
    task_id: str


class ListTasksQuery(BaseModel):
    project_id: str | None = None
    status: TaskStatus | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class Task(BaseModel):
    id: str
    project_id: str
    title: str
    status: TaskStatus


class TaskPage(BaseModel):
    items: list[Task]
    total: int
    limit: int
    offset: int
