from uuid import uuid4

from app.features.todos.schemas import Todo
from app.shared.users import OwnerScope


class MemoryTodoRepository:
    """In-memory implementation of the owner-scoped repository port."""

    def __init__(self) -> None:
        self._todos: dict[str, tuple[Todo, str]] = {}

    async def create(self, *, title: str, owner: OwnerScope) -> Todo:
        todo = Todo(id=uuid4().hex, title=title, completed=False)
        self._todos[todo.id] = (todo, owner.owner_id)
        return todo

    async def list(self, *, owner: OwnerScope) -> list[Todo]:
        return [
            todo
            for todo, owner_id in self._todos.values()
            if owner_id == owner.owner_id
        ]
