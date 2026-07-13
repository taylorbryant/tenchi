from app.features.todos.ports import TodoRepository

from .memory_todo_repository import MemoryTodoRepository


def create_todo_repository() -> TodoRepository:
    return MemoryTodoRepository()
