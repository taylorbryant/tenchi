from typing import Protocol

from .schemas import Project


class ProjectRepository(Protocol):
    async def create(self, *, name: str, owner_id: str) -> Project: ...

    async def get(self, project_id: str) -> Project | None: ...

    async def list_owned_by(self, owner_id: str) -> list[Project]: ...
