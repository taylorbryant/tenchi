from dataclasses import dataclass

from app.features.projects.ports import NotificationLog, Outbox, ProjectRepository
from app.features.tasks.ports import TaskRepository
from app.shared.users import User


@dataclass(frozen=True, slots=True)
class AppContext:
    projects: ProjectRepository
    tasks: TaskRepository
    outbox: Outbox
    notifications: NotificationLog
    user: User | None = None
