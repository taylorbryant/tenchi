from dataclasses import dataclass

from app.features.projects.ports import ProjectRepository
from app.features.tasks.ports import TaskRepository
from app.shared.users import User


@dataclass(frozen=True, slots=True)
class AppContext:
    projects: ProjectRepository
    tasks: TaskRepository
    user: User | None = None
