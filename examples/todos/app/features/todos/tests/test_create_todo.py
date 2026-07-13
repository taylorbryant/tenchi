from app.features.todos.schemas import CreateTodo
from app.features.todos.use_cases.create_todo import create_todo
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext


async def test_create_todo_persists_through_the_repository_port() -> None:
    repository = MemoryTodoRepository()
    context = AppContext(todos=repository)

    todo = await create_todo(CreateTodo(title="Buy milk"), context)

    assert todo.title == "Buy milk"
    assert todo.completed is False
    assert await repository.get(todo.id) == todo
