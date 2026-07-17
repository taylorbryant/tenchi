"""In-memory implementations of the taskboard ports, for tests."""

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from app.features.projects.schemas import Project
from app.features.tasks.schemas import Task, TaskStatus
from app.shared.users import OwnerScope


class MemoryOutbox:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, *, job: str, payload: Mapping[str, Any]) -> None:
        self.entries.append((job, dict(payload)))


class MemoryNotificationLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    async def record(self, *, user_id: str, message: str) -> None:
        self.records.append((user_id, message))


class MemoryProjectRepository:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}

    async def create(self, *, name: str, owner: OwnerScope) -> Project:
        project = Project(id=uuid4().hex, name=name, owner_id=owner.owner_id)
        self.projects[project.id] = project
        return project

    async def get(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    async def save(self, project: Project) -> Project:
        self.projects[project.id] = project
        return project

    async def list_owned_by(self, owner: OwnerScope) -> list[Project]:
        return [p for p in self.projects.values() if p.owner_id == owner.owner_id]


class MemoryTaskRepository:
    """Task store; ownership scoping resolves through the project store."""

    def __init__(self, projects: MemoryProjectRepository) -> None:
        self._projects = projects
        self.tasks: dict[str, Task] = {}
        self._create_requests: dict[tuple[str, str], tuple[str, Task]] = {}

    async def create(self, *, project_id: str, title: str) -> Task:
        task = Task(
            id=uuid4().hex,
            project_id=project_id,
            title=title,
            status=TaskStatus.TODO,
            version=1,
        )
        self.tasks[task.id] = task
        return task

    async def create_idempotent(
        self,
        *,
        project_id: str,
        title: str,
        owner: OwnerScope,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Task | None:
        scope = (owner.owner_id, idempotency_key)
        existing = self._create_requests.get(scope)
        if existing is not None:
            fingerprint, original = existing
            return (
                original.model_copy(deep=True)
                if fingerprint == request_fingerprint
                else None
            )

        task = await self.create(project_id=project_id, title=title)
        self._create_requests[scope] = (
            request_fingerprint,
            task.model_copy(deep=True),
        )
        return task

    async def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def save(self, task: Task, *, expected_version: int) -> Task | None:
        current = self.tasks.get(task.id)
        if current is None or current.version != expected_version:
            return None
        saved = task.model_copy(update={"version": expected_version + 1})
        self.tasks[task.id] = saved
        return saved


class MemoryTaskSearch:
    """Read-side counterpart of MemoryTaskRepository, over the same stores."""

    def __init__(
        self, projects: MemoryProjectRepository, tasks: MemoryTaskRepository
    ) -> None:
        self._projects = projects
        self._tasks = tasks

    async def search(
        self,
        *,
        viewer: OwnerScope,
        project_id: str | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        visible = {
            p.id
            for p in self._projects.projects.values()
            if p.owner_id == viewer.owner_id or viewer.owner_id in p.member_ids
        }
        matches = [
            task
            for task in self._tasks.tasks.values()
            if task.project_id in visible
            and (project_id is None or task.project_id == project_id)
            and (status is None or task.status == status)
        ]
        return matches[offset : offset + limit], len(matches)
