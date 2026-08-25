from typing import Protocol

from app.shared.users import OwnerScope

from .schemas import Todo


class TodoRepository(Protocol):
    async def create(self, *, title: str, owner: OwnerScope) -> Todo: ...

    async def list(self, *, owner: OwnerScope) -> list[Todo]: ...
