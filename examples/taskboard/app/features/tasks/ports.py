from typing import Protocol

from app.shared.users import OwnerScope

from .schemas import Task, TaskStatus


class TaskRepository(Protocol):
    async def create(self, *, project_id: str, title: str) -> Task: ...

    async def get(self, task_id: str) -> Task | None: ...

    async def save(self, task: Task) -> Task: ...

    async def search(
        self,
        *,
        owner: OwnerScope,
        project_id: str | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        """Return one page of the owner's tasks and the total match count."""
        ...
