from collections.abc import Mapping
from typing import Any, Protocol

from app.shared.users import OwnerScope

from .schemas import Project


class Outbox(Protocol):
    """Deferred effects, enqueued in the request's unit of work.

    The production adapter writes to an outbox table on the request's
    transactional connection, so a job is persisted exactly when the
    state change it announces is. A worker delivers it later.
    """

    async def enqueue(self, *, job: str, payload: Mapping[str, Any]) -> None: ...


class NotificationLog(Protocol):
    """Where user-facing notifications land (a stand-in for email/push)."""

    async def record(self, *, user_id: str, message: str) -> None: ...


class ProjectRepository(Protocol):
    async def create(self, *, name: str, owner: OwnerScope) -> Project: ...

    async def get(self, project_id: str) -> Project | None: ...

    async def save(self, project: Project) -> Project: ...

    async def list_owned_by(self, owner: OwnerScope) -> list[Project]: ...
