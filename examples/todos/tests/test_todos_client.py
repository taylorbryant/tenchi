"""The typed client exercising the todos app over ASGI."""

from collections.abc import AsyncIterator

import httpx
import pytest

from app.features.todos.contracts import (
    create_todo_contract,
    get_todo_contract,
    list_todos_contract,
)
from app.features.todos.schemas import (
    CreateTodo,
    GetTodoParams,
    ListTodosQuery,
    Todo,
)
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.server.routes import routes
from app.shared.errors import todo_not_found
from tenchi.client import Client
from tenchi.errors import AppError
from tenchi.server import create_app


@pytest.fixture
async def client() -> AsyncIterator[Client]:
    repository = MemoryTodoRepository()
    app = create_app(
        routes=routes,
        context_factory=lambda: AppContext(todos=repository),
    )
    async with Client(transport=httpx.ASGITransport(app=app)) as tenchi_client:
        yield tenchi_client


async def test_full_flow_through_typed_client(client: Client) -> None:
    created = await client.call(
        create_todo_contract, request=CreateTodo(title="Buy milk")
    )
    assert isinstance(created, Todo)

    fetched = await client.call(
        get_todo_contract, params=GetTodoParams(todo_id=created.id)
    )
    assert fetched == created

    open_todos = await client.call(
        list_todos_contract, query=ListTodosQuery(completed=False)
    )
    assert open_todos == [created]


async def test_declared_error_surfaces_as_app_error(client: Client) -> None:
    with pytest.raises(AppError) as excinfo:
        await client.call(get_todo_contract, params=GetTodoParams(todo_id="missing"))

    assert excinfo.value.definition == todo_not_found
