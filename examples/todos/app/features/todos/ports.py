from typing import Protocol

from .schemas import Todo


class TodoRepository(Protocol):
    async def create(self, *, title: str) -> Todo: ...

    async def get(self, todo_id: str) -> Todo | None: ...

    async def list(self) -> list[Todo]: ...
