from uuid import uuid4

from app.features.todos.schemas import Todo


class MemoryTodoRepository:
    """In-memory implementation of the ``TodoRepository`` port."""

    def __init__(self) -> None:
        self._todos: dict[str, Todo] = {}

    async def create(self, *, title: str) -> Todo:
        todo = Todo(id=uuid4().hex, title=title, completed=False)
        self._todos[todo.id] = todo
        return todo

    async def get(self, todo_id: str) -> Todo | None:
        return self._todos.get(todo_id)

    async def list(self) -> list[Todo]:
        return list(self._todos.values())
