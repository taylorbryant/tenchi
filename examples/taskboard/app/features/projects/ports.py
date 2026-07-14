from typing import Protocol

from app.shared.users import OwnerScope

from .schemas import Project


class ProjectRepository(Protocol):
    async def create(self, *, name: str, owner: OwnerScope) -> Project: ...

    async def get(self, project_id: str) -> Project | None: ...

    async def save(self, project: Project) -> Project: ...

    async def list_owned_by(self, owner: OwnerScope) -> list[Project]: ...
