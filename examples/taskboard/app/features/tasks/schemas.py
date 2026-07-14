from enum import StrEnum

from pydantic import BaseModel, Field

from tenchi.pagination import PageQuery


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


class ListTasksQuery(PageQuery):
    project_id: str | None = None
    status: TaskStatus | None = None


class Task(BaseModel):
    id: str
    project_id: str
    title: str
    status: TaskStatus
