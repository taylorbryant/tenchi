from app.features.todos.schemas import CreateTodo, ListTodosQuery
from app.features.todos.use_cases.create_todo import create_todo
from app.features.todos.use_cases.list_todos import list_todos
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext


async def test_list_todos_returns_created_todos() -> None:
    context = AppContext(todos=MemoryTodoRepository())

    assert await list_todos(ListTodosQuery(), context) == []

    first = await create_todo(CreateTodo(title="one"), context)
    second = await create_todo(CreateTodo(title="two"), context)

    assert await list_todos(ListTodosQuery(), context) == [first, second]


async def test_list_todos_filters_by_completed() -> None:
    context = AppContext(todos=MemoryTodoRepository())
    created = await create_todo(CreateTodo(title="one"), context)

    assert await list_todos(ListTodosQuery(completed=False), context) == [created]
    assert await list_todos(ListTodosQuery(completed=True), context) == []
