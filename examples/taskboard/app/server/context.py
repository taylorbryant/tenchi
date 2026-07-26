from dataclasses import dataclass

from app.features.projects.ports import NotificationLog, Outbox, ProjectRepository
from app.features.tasks.ports import TaskRepository, TaskSearch
from app.shared.users import User
from tenchi.idempotency import IdempotencyStore


@dataclass(frozen=True, slots=True)
class AppContext:
    projects: ProjectRepository
    tasks: TaskRepository
    task_search: TaskSearch
    idempotency: IdempotencyStore
    outbox: Outbox
    notifications: NotificationLog
    user: User | None = None
