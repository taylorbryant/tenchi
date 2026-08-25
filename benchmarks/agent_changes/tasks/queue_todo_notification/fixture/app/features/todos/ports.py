from typing import Protocol

from .schemas import Todo


class TodoRepository(Protocol):
    async def create(self, *, title: str) -> Todo: ...

    async def list(self) -> list[Todo]: ...


class Outbox(Protocol):
    """Durable messages written in the state change's transaction."""

    async def enqueue(self, *, job: str, payload_json: bytes) -> None: ...
