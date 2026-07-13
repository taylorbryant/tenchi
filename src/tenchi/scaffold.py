"""File templates for ``tenchi new``.

The scaffold is the prescribed starter subset: one todos feature with
create/list use cases, protocol ports, a memory adapter, explicit wiring,
and tests. Templates use ``__APP_NAME__`` as the only substitution marker so
no escaping of braces or brackets is needed.
"""

from __future__ import annotations

_PYPROJECT = """\
[project]
name = "__APP_NAME__"
version = "0.1.0"
requires-python = ">=3.12"
# While Tenchi is unpublished, point uv at your checkout, for example:
#   [tool.uv.sources]
#   tenchi = { path = "../tenchi", editable = true }
dependencies = ["tenchi"]

[dependency-groups]
dev = [
    "httpx>=0.27",
    "pyright>=1.1.390",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
    "uvicorn>=0.30",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "app"]
pythonpath = ["."]

[tool.ruff]
line-length = 88

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.pyright]
include = ["app", "tests"]
typeCheckingMode = "strict"
pythonVersion = "3.12"
"""

_README = """\
# __APP_NAME__

A [Tenchi](https://github.com/taylorbryant/tenchi) application.

```sh
uv sync                 # install dependencies
uv run pytest           # run tests
uv run tenchi dev       # run the server with reload
uv run tenchi routes    # list bound routes
uv run tenchi openapi   # print the OpenAPI document
```
"""

_GITIGNORE = """\
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
"""

_SCHEMAS = """\
from pydantic import BaseModel, Field


class CreateTodo(BaseModel):
    title: str = Field(min_length=1)


class Todo(BaseModel):
    id: str
    title: str
    completed: bool
"""

_PORTS = """\
from typing import Protocol

from .schemas import Todo


class TodoRepository(Protocol):
    async def create(self, *, title: str) -> Todo: ...

    async def list(self) -> list[Todo]: ...
"""

_SHARED_ERRORS = """\
\"\"\"Application error definitions.

Declare errors here, raise them from use cases with AppError, and list them
on the contracts that are expected to return them.
\"\"\"

from tenchi.errors import ErrorDef

todo_not_found = ErrorDef(
    code="TODO_NOT_FOUND",
    status=404,
    message="Todo not found",
)
"""

_CONTRACTS = """\
from tenchi.contracts import contract

from .schemas import CreateTodo, Todo

create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    status=201,
)

list_todos_contract = contract(
    method="GET",
    path="/todos",
    response=list[Todo],
)
"""

_USE_CASE_CREATE = """\
from app.server.context import AppContext

from ..schemas import CreateTodo, Todo


async def create_todo(request: CreateTodo, context: AppContext) -> Todo:
    return await context.todos.create(title=request.title)
"""

_USE_CASE_LIST = """\
from app.server.context import AppContext

from ..schemas import Todo


async def list_todos(context: AppContext) -> list[Todo]:
    return await context.todos.list()
"""

_FEATURE_ROUTES = """\
from tenchi.routes import route, route_group

from .contracts import create_todo_contract, list_todos_contract
from .use_cases.create_todo import create_todo
from .use_cases.list_todos import list_todos

routes = route_group(
    route(create_todo_contract, create_todo),
    route(list_todos_contract, list_todos),
)
"""

_FEATURE_TEST = """\
from app.features.todos.schemas import CreateTodo
from app.features.todos.use_cases.create_todo import create_todo
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext


async def test_create_todo_persists_through_the_repository_port() -> None:
    context = AppContext(todos=MemoryTodoRepository())

    todo = await create_todo(CreateTodo(title="Buy milk"), context)

    assert todo.title == "Buy milk"
    assert todo.completed is False
"""

_MEMORY_REPOSITORY = """\
from uuid import uuid4

from app.features.todos.schemas import Todo


class MemoryTodoRepository:
    \"\"\"In-memory implementation of the ``TodoRepository`` port.\"\"\"

    def __init__(self) -> None:
        self._todos: dict[str, Todo] = {}

    async def create(self, *, title: str) -> Todo:
        todo = Todo(id=uuid4().hex, title=title, completed=False)
        self._todos[todo.id] = todo
        return todo

    async def list(self) -> list[Todo]:
        return list(self._todos.values())
"""

_PORT_WIRING = """\
from app.features.todos.ports import TodoRepository

from .memory_todo_repository import MemoryTodoRepository


def create_todo_repository() -> TodoRepository:
    return MemoryTodoRepository()
"""

_CONTEXT = """\
from dataclasses import dataclass

from app.features.todos.ports import TodoRepository


@dataclass(frozen=True, slots=True)
class AppContext:
    todos: TodoRepository
"""

_SERVER_ROUTES = """\
from tenchi.routes import route_group

from app.features.todos.routes import routes as todo_routes

routes = route_group(todo_routes)
"""

_SERVER_APP = """\
\"\"\"Server composition: concrete wiring and the ASGI application.

Run locally with:

    uv run tenchi dev
\"\"\"

from tenchi.server import create_app

from app.infra.port_wiring import create_todo_repository
from app.server.context import AppContext
from app.server.routes import routes

# Repositories are process-scoped; the context wrapping them is rebuilt for
# every request by ``create_context``.
todo_repository = create_todo_repository()


def create_context() -> AppContext:
    return AppContext(todos=todo_repository)


app = create_app(routes=routes, context_factory=create_context)
"""

_HTTP_TEST = """\
from collections.abc import AsyncIterator

import httpx
import pytest
from tenchi.server import create_app

from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext
from app.server.routes import routes


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    repository = MemoryTodoRepository()
    app = create_app(
        routes=routes,
        context_factory=lambda: AppContext(todos=repository),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


async def test_create_and_list_todos(client: httpx.AsyncClient) -> None:
    created = await client.post("/todos", json={"title": "Buy milk"})
    assert created.status_code == 201

    listed = await client.get("/todos")
    assert listed.status_code == 200
    assert listed.json() == [created.json()]
"""

_FILES: dict[str, str] = {
    "pyproject.toml": _PYPROJECT,
    "README.md": _README,
    ".gitignore": _GITIGNORE,
    "app/__init__.py": "",
    "app/features/__init__.py": "",
    "app/features/todos/__init__.py": "",
    "app/features/todos/contracts.py": _CONTRACTS,
    "app/features/todos/ports.py": _PORTS,
    "app/features/todos/routes.py": _FEATURE_ROUTES,
    "app/features/todos/schemas.py": _SCHEMAS,
    "app/features/todos/tests/__init__.py": "",
    "app/features/todos/tests/test_create_todo.py": _FEATURE_TEST,
    "app/features/todos/use_cases/__init__.py": "",
    "app/features/todos/use_cases/create_todo.py": _USE_CASE_CREATE,
    "app/features/todos/use_cases/list_todos.py": _USE_CASE_LIST,
    "app/infra/__init__.py": "",
    "app/infra/memory_todo_repository.py": _MEMORY_REPOSITORY,
    "app/infra/port_wiring.py": _PORT_WIRING,
    "app/server/__init__.py": "",
    "app/server/asgi.py": _SERVER_APP,
    "app/server/context.py": _CONTEXT,
    "app/server/routes.py": _SERVER_ROUTES,
    "app/shared/__init__.py": "",
    "app/shared/errors.py": _SHARED_ERRORS,
    "tests/test_http.py": _HTTP_TEST,
}


def app_files(app_name: str) -> dict[str, str]:
    """Return the scaffold as a mapping of relative path to file content."""
    return {
        path: content.replace("__APP_NAME__", app_name)
        for path, content in _FILES.items()
    }


_MAKE_FEATURE_ROUTES = '''\
"""Routes for the __FEATURE__ feature.

Bind contracts to use cases with route(contract, use_case), then compose
this group in app/server/routes.py.
"""

from tenchi.routes import route_group

routes = route_group()
'''


def feature_files(feature: str) -> dict[str, str]:
    """Return a feature skeleton, relative to ``app/features/<feature>/``."""
    return {
        "__init__.py": "",
        "schemas.py": f'"""Pydantic models for the {feature} feature."""\n',
        "ports.py": (
            f'"""Dependency protocols the {feature} feature needs, '
            'implemented in app/infra/."""\n'
        ),
        "contracts.py": f'"""HTTP contracts for the {feature} feature."""\n',
        "routes.py": _MAKE_FEATURE_ROUTES.replace("__FEATURE__", feature),
        "use_cases/__init__.py": "",
        "tests/__init__.py": "",
    }


_USE_CASE = """\
from app.server.context import AppContext


async def __NAME__(context: AppContext) -> None:
    raise NotImplementedError
"""

_USE_CASE_TEST = """\
import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement __NAME__")


async def test___NAME__() -> None:
    raise NotImplementedError
"""


def use_case_files(feature: str, name: str) -> dict[str, str]:
    """Return a use-case stub and test, relative to the feature directory."""
    substitutions = {"__FEATURE__": feature, "__NAME__": name}
    files = {
        f"use_cases/{name}.py": _USE_CASE,
        f"tests/test_{name}.py": _USE_CASE_TEST,
    }
    rendered: dict[str, str] = {}
    for path, content in files.items():
        for marker, value in substitutions.items():
            content = content.replace(marker, value)
        rendered[path] = content
    return rendered
