"""In-memory implementations of the taskboard ports, for tests."""

from uuid import uuid4

from app.features.projects.schemas import Project
from app.features.tasks.schemas import Task, TaskStatus
from app.shared.users import OwnerScope


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

    async def create(self, *, project_id: str, title: str) -> Task:
        task = Task(
            id=uuid4().hex,
            project_id=project_id,
            title=title,
            status=TaskStatus.TODO,
        )
        self.tasks[task.id] = task
        return task

    async def get(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    async def save(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    async def search(
        self,
        *,
        owner: OwnerScope,
        project_id: str | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        owned = {
            p.id
            for p in self._projects.projects.values()
            if p.owner_id == owner.owner_id
        }
        matches = [
            task
            for task in self.tasks.values()
            if task.project_id in owned
            and (project_id is None or task.project_id == project_id)
            and (status is None or task.status == status)
        ]
        return matches[offset : offset + limit], len(matches)
