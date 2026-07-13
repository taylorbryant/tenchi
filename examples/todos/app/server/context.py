from dataclasses import dataclass

from app.features.todos.ports import TodoRepository


@dataclass(frozen=True, slots=True)
class AppContext:
    todos: TodoRepository
