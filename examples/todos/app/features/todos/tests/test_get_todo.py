import pytest

from app.features.todos.schemas import CreateTodo, GetTodoParams
from app.features.todos.use_cases.create_todo import create_todo
from app.features.todos.use_cases.get_todo import get_todo
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.shared.errors import todo_not_found
from tenchi.errors import AppError


async def test_get_todo_returns_the_todo() -> None:
    context = AppContext(todos=MemoryTodoRepository())
    created = await create_todo(CreateTodo(title="Buy milk"), context)

    found = await get_todo(GetTodoParams(todo_id=created.id), context)

    assert found == created


async def test_get_todo_raises_expected_error_when_missing() -> None:
    context = AppContext(todos=MemoryTodoRepository())

    with pytest.raises(AppError) as excinfo:
        await get_todo(GetTodoParams(todo_id="missing"), context)

    assert excinfo.value.definition == todo_not_found
    assert excinfo.value.details == {"todo_id": "missing"}
