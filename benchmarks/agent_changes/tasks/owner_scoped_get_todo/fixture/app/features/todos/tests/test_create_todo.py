from app.features.todos.schemas import CreateTodo
from app.features.todos.use_cases.create_todo import create_todo
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.shared.users import User


async def test_create_todo_persists_for_the_authenticated_owner() -> None:
    repository = MemoryTodoRepository()
    context = AppContext(
        todos=repository,
        user=User(id="alice", name="Alice"),
    )

    todo = await create_todo(CreateTodo(title="Buy milk"), context)

    assert todo.title == "Buy milk"
    assert todo.completed is False
