from dataclasses import dataclass

from app.features.todos.ports import TodoRepository
from app.shared.users import User


@dataclass(frozen=True, slots=True)
class AppContext:
    todos: TodoRepository
    user: User | None = None
