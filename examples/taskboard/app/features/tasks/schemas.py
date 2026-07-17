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


IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class CreateTaskHeaders(BaseModel):
    """A stable key for safely retrying one logical create command."""

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=IDEMPOTENCY_KEY_PATTERN,
    )


class UpdateTask(BaseModel):
    """Partial update: ``None`` fields are left unchanged."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: TaskStatus | None = None


TASK_ETAG_PATTERN = r'^"[1-9][0-9]*"$'


class UpdateTaskHeaders(BaseModel):
    """A strong entity tag identifying the task revision being updated."""

    if_match: str | None = Field(default=None, pattern=TASK_ETAG_PATTERN)

    @property
    def expected_version(self) -> int | None:
        if self.if_match is None:
            return None
        return int(self.if_match[1:-1])


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
    version: int = Field(ge=1)
