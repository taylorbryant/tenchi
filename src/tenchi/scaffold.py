"""File templates for ``tenchi new``.

The scaffold is the prescribed starter subset: one todos feature with
create/list use cases, protocol ports, SQLite runtime persistence, a memory
test adapter, explicit lifespan wiring, service routes, CI, and tests.
"""

from __future__ import annotations

_PYPROJECT = """\
[project]
name = "__APP_NAME__"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["aiosqlite>=0.20", "tenchi"]

[dependency-groups]
dev = [
    "httpx>=0.27",
    "pyright>=1.1.390",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
    "tenchi[mcp]",
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
uv run tenchi check     # run every project check
uv run tenchi dev       # run the server with reload
uv run tenchi routes    # list bound routes
uv run tenchi map       # inspect the complete application graph
uv run tenchi mcp       # serve Tenchi tools to MCP-aware coding agents
uv run tenchi openapi --routes app.server.routes:api_routes \\
  --title __APP_NAME__ --diff openapi.json
uv run tenchi openapi --routes app.server.routes:api_routes \\
  --title __APP_NAME__ --check openapi.json
uv run tenchi openapi --routes app.server.routes:api_routes \\
  --title __APP_NAME__ --write openapi.json
uv run tenchi doctor    # check dependency direction and structure
```

Run `openapi --diff` before using `openapi --write` to replace the baseline. In
CI, the generated workflow uses `--diff-ref` to compare against the pull
request's base commit rather than the snapshot committed in the same change.

The API persists to `__APP_NAME__.db` by default. Override the location with
`__APP_ENV_PREFIX___DATABASE`. With the development server running, browse
Swagger UI at http://127.0.0.1:8000/docs.

The checked-in `.mcp.json` registers `uv run tenchi mcp --root .` for clients
that support project-local MCP configuration. The same inspection and
validation operations remain available through the CLI.
"""

_AGENTS = """\
# Tenchi application guide

This application uses Tenchi's prescribed architecture: contracts define the
HTTP boundary, plain async use cases own behavior, protocols describe required
infrastructure, and server composition wires concrete adapters.

Framework agent workflow: https://tenchi.io/agents

## Working loop

1. Run `uv run tenchi map --feature <name> --json`, then read the feature's
   schemas, contracts, routes, use cases, ports, and tests before changing it.
2. Prefer `uv run tenchi make feature <name> --dry-run` and
   `uv run tenchi make use-case <feature> <name> --dry-run` before creating
   framework-shaped files manually.
3. Keep explicit wiring visible in `app/server/routes.py`,
   `app/infra/port_wiring.py`, and `app/server/asgi.py`.
4. Run `uv run tenchi check` after a coherent change and treat every failed
   step as unfinished work.

Use `--json` with `tenchi map`, `tenchi routes`, `tenchi doctor`, `tenchi
check`, and `tenchi make ...` when structured output is more useful than
terminal text.

For MCP-aware agents, `.mcp.json` registers the app-local Tenchi server. Its
`app_map`, `routes`, `doctor`, `openapi_diff`, `make_preview`, and `check` tools
return the same versioned results. Inspection and preview tools never write
application files; `check` runs the project's normal validation commands. The
agent still makes ordinary, reviewable source edits.

## Placement and dependency direction

- `app/features/<feature>/contracts.py` owns HTTP declarations.
- `schemas.py` owns Pydantic boundary and domain models.
- `ports.py` owns `typing.Protocol` interfaces needed by the feature.
- `policy.py` owns pure authorization rules for subjects in the feature.
- `routes.py` binds contracts to use cases; it never imports infrastructure.
- `use_cases/` contains one plain async function per workflow. Use cases may
  depend on schemas, ports, policies, shared code, and `app.server.context`, but
  never concrete infrastructure, routes, or the Tenchi/Starlette runtime.
- `app/infra/` implements ports and never imports use cases, contracts, routes,
  or server composition.
- `app/server/` is the composition root and may import every application layer.
- `app/shared/` never imports features.

Authentication belongs in boundary hooks. Authorization belongs in use cases
and pure policy functions. Declare every expected `AppError` on its contract or
route group; undeclared application errors intentionally become framework 500s.

## Change checklist

- Update the contract, use case, route binding, tests, and OpenAPI snapshot
  together when an operation changes.
- Test use cases directly with memory adapters; use `tenchi.testing` for HTTP
  integration tests so lifespan resources run.
- Run `tenchi openapi --routes app.server.routes:api_routes --title __APP_NAME__
  --version 0.1.0 --diff openapi.json` before replacing the snapshot with
  `--write`; preserve those metadata flags when writing it.
- Do not hand-edit generated files into a different application structure to
  avoid a doctor finding; fix the dependency or placement problem instead.
"""

_GITIGNORE = """\
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
*.db
*.db-shm
*.db-wal
"""

_MCP_JSON = """\
{
  "mcpServers": {
    "tenchi": {
      "command": "uv",
      "args": ["run", "tenchi", "mcp", "--root", "."]
    }
  }
}
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
from pydantic import BaseModel, Field
from tenchi.contracts import contract

from .schemas import CreateTodo, Todo


class CreatedTodoHeaders(BaseModel):
    location: str = Field(alias="Location")


create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    response_headers=CreatedTodoHeaders,
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

from .contracts import CreatedTodoHeaders, create_todo_contract, list_todos_contract
from .schemas import Todo
from .use_cases.create_todo import create_todo
from .use_cases.list_todos import list_todos


def create_todo_headers(todo: Todo) -> CreatedTodoHeaders:
    return CreatedTodoHeaders(Location=f"/todos/{todo.id}")


routes = route_group(
    route(
        create_todo_contract,
        create_todo,
        response_headers=create_todo_headers,
    ),
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

_SQLITE_REPOSITORY = """\
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import aiosqlite

from app.features.todos.schemas import Todo

_SCHEMA = \"\"\"
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0
)
\"\"\"


class SqliteTodoRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create(self, *, title: str) -> Todo:
        todo = Todo(id=uuid4().hex, title=title, completed=False)
        await self._connection.execute(
            "INSERT INTO todos (id, title, completed) VALUES (?, ?, ?)",
            (todo.id, todo.title, int(todo.completed)),
        )
        return todo

    async def list(self) -> list[Todo]:
        cursor = await self._connection.execute(
            "SELECT id, title, completed FROM todos ORDER BY rowid"
        )
        rows = await cursor.fetchall()
        return [_row_to_todo(row) for row in rows]


async def ensure_sqlite_todo_schema(database_path: str) -> None:
    async with aiosqlite.connect(database_path) as connection:
        await _configure_connection(connection)
        await connection.execute(_SCHEMA)
        await connection.commit()


@asynccontextmanager
async def open_sqlite_todo_repository(
    database_path: str,
) -> AsyncGenerator[SqliteTodoRepository]:
    async with aiosqlite.connect(database_path) as connection:
        await _configure_connection(connection)
        try:
            yield SqliteTodoRepository(connection)
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise


async def _configure_connection(connection: aiosqlite.Connection) -> None:
    await connection.execute("PRAGMA busy_timeout = 5000")


def _row_to_todo(row: Any) -> Todo:
    return Todo(id=row[0], title=row[1], completed=bool(row[2]))
"""

_PORT_WIRING = """\
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from app.features.todos.ports import TodoRepository

from .sqlite_todo_repository import (
    ensure_sqlite_todo_schema,
    open_sqlite_todo_repository,
)


async def ensure_schema(database_path: str) -> None:
    await ensure_sqlite_todo_schema(database_path)


@asynccontextmanager
async def open_todo_repository(
    database_path: str,
) -> AsyncGenerator[TodoRepository]:
    async with open_sqlite_todo_repository(database_path) as repository:
        yield repository
"""

_CONTEXT = """\
from dataclasses import dataclass

from app.features.todos.ports import TodoRepository


@dataclass(frozen=True, slots=True)
class AppContext:
    todos: TodoRepository
"""

_SERVER_ROUTES = """\
from tenchi.health import health_route
from tenchi.openapi import openapi_route, swagger_ui_route
from tenchi.routes import route_group

from app.features.todos.routes import routes as todo_routes

OPENAPI_TITLE = "__APP_NAME__"
OPENAPI_VERSION = "0.1.0"
OPENAPI_DESCRIPTION: str | None = None

api_routes = route_group(todo_routes)

routes = route_group(
    api_routes,
    openapi_route(
        api_routes,
        title=OPENAPI_TITLE,
        version=OPENAPI_VERSION,
        description=OPENAPI_DESCRIPTION,
    ),
    swagger_ui_route(title=f"{OPENAPI_TITLE} API"),
    health_route(),
)
"""

_SERVER_APP = """\
\"\"\"Server composition: concrete wiring and the ASGI application.

Run locally with:

    uv run tenchi dev
\"\"\"

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from tenchi.server import create_app

from app.infra.port_wiring import ensure_schema, open_todo_repository
from app.server.context import AppContext
from app.server.routes import routes

DATABASE_PATH = os.environ.get("__APP_ENV_PREFIX___DATABASE", "__APP_NAME__.db")


def build_app(database_path: str = DATABASE_PATH) -> Starlette:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        await ensure_schema(database_path)
        yield database_path

    @asynccontextmanager
    async def create_context(path: str) -> AsyncGenerator[AppContext]:
        async with open_todo_repository(path) as todos:
            yield AppContext(todos=todos)

    return create_app(
        routes=routes,
        context_factory=create_context,
        lifespan=lifespan,
    )


app = build_app()
"""

_HTTP_TEST = """\
from pathlib import Path

import pytest
from tenchi.testing import open_client, open_http

from app.features.todos.contracts import create_todo_contract
from app.features.todos.schemas import CreateTodo
from app.infra.port_wiring import ensure_schema, open_todo_repository
from app.server.asgi import build_app


async def test_create_and_list_todos_across_a_restart(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    async with open_client(build_app(database_path)) as client:
        created = await client.call_with_response(
            create_todo_contract,
            request=CreateTodo(title="Buy milk"),
        )

    assert created.headers.location == f"/todos/{created.body.id}"

    async with open_http(build_app(database_path)) as http:
        listed = await http.get("/todos")

    assert listed.status_code == 200
    assert listed.json() == [created.body.model_dump()]


async def test_service_routes_are_available(tmp_path: Path) -> None:
    async with open_http(build_app(str(tmp_path / "todos.db"))) as http:
        health = await http.get("/health")
        openapi = await http.get("/openapi.json")
        docs = await http.get("/docs")

    assert health.status_code == 200
    assert openapi.status_code == 200
    assert docs.headers["content-type"] == "text/html; charset=utf-8"


async def test_failed_repository_scope_rolls_back(tmp_path: Path) -> None:
    database_path = str(tmp_path / "todos.db")
    await ensure_schema(database_path)

    with pytest.raises(RuntimeError, match="abort request"):
        async with open_todo_repository(database_path) as todos:
            await todos.create(title="Do not persist")
            raise RuntimeError("abort request")

    async with open_todo_repository(database_path) as todos:
        assert await todos.list() == []
"""

_OPENAPI_TEST = """\
from tenchi.cli import main

from app.server.routes import OPENAPI_DESCRIPTION, OPENAPI_TITLE, OPENAPI_VERSION

OPENAPI_ARGS = [
    "openapi",
    "--routes",
    "app.server.routes:api_routes",
    "--title",
    OPENAPI_TITLE,
    "--version",
    OPENAPI_VERSION,
]
if OPENAPI_DESCRIPTION is not None:
    OPENAPI_ARGS.extend(("--description", OPENAPI_DESCRIPTION))


def test_openapi_snapshot_is_current() -> None:
    assert (
        main(
            [
                *OPENAPI_ARGS,
                "--check",
                "openapi.json",
            ]
        )
        == 0
    )
"""

_CI_WORKFLOW = """\
name: CI

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv sync
      - run: uv run tenchi check
      - name: Check OpenAPI compatibility
        if: github.event_name == 'pull_request'
        run: >-
          uv run tenchi openapi
          --routes app.server.routes:api_routes
          --title __APP_NAME__ --version 0.1.0
          --diff-ref "${{ github.event.pull_request.base.sha }}"
          --snapshot openapi.json
"""

_OPENAPI_SNAPSHOT = """\
{
  "components": {
    "schemas": {
      "ErrorResponse": {
        "properties": {
          "code": {
            "type": "string"
          },
          "details": {},
          "message": {
            "type": "string"
          },
          "request_id": {
            "type": "string"
          }
        },
        "required": [
          "code",
          "message"
        ],
        "title": "ErrorResponse",
        "type": "object"
      },
      "Todo": {
        "properties": {
          "completed": {
            "title": "Completed",
            "type": "boolean"
          },
          "id": {
            "title": "Id",
            "type": "string"
          },
          "title": {
            "title": "Title",
            "type": "string"
          }
        },
        "required": [
          "id",
          "title",
          "completed"
        ],
        "title": "Todo",
        "type": "object"
      }
    }
  },
  "info": {
    "title": "__APP_NAME__",
    "version": "0.1.0"
  },
  "openapi": "3.1.0",
  "paths": {
    "/todos": {
      "get": {
        "operationId": "list_todos",
        "responses": {
          "200": {
            "content": {
              "application/json": {
                "schema": {
                  "items": {
                    "$ref": "#/components/schemas/Todo"
                  },
                  "type": "array"
                }
              }
            },
            "description": "Successful response"
          }
        }
      },
      "post": {
        "operationId": "create_todo",
        "requestBody": {
          "content": {
            "application/json": {
              "schema": {
                "properties": {
                  "title": {
                    "minLength": 1,
                    "title": "Title",
                    "type": "string"
                  }
                },
                "required": [
                  "title"
                ],
                "title": "CreateTodo",
                "type": "object"
              }
            }
          },
          "required": true
        },
        "responses": {
          "201": {
            "content": {
              "application/json": {
                "schema": {
                  "properties": {
                    "completed": {
                      "title": "Completed",
                      "type": "boolean"
                    },
                    "id": {
                      "title": "Id",
                      "type": "string"
                    },
                    "title": {
                      "title": "Title",
                      "type": "string"
                    }
                  },
                  "required": [
                    "id",
                    "title",
                    "completed"
                  ],
                  "title": "Todo",
                  "type": "object"
                }
              }
            },
            "description": "Successful response",
            "headers": {
              "Location": {
                "required": true,
                "schema": {
                  "title": "Location",
                  "type": "string"
                }
              }
            }
          },
          "413": {
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ErrorResponse"
                }
              }
            },
            "description": "REQUEST_TOO_LARGE: Request body too large"
          },
          "415": {
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ErrorResponse"
                }
              }
            },
            "description": "__UNSUPPORTED_MEDIA_TYPE_DESCRIPTION__"
          },
          "422": {
            "content": {
              "application/json": {
                "schema": {
                  "$ref": "#/components/schemas/ErrorResponse"
                }
              }
            },
            "description": "VALIDATION_ERROR: Request validation failed"
          }
        }
      }
    }
  }
}
"""

_FILES: dict[str, str] = {
    "pyproject.toml": _PYPROJECT,
    "README.md": _README,
    "AGENTS.md": _AGENTS,
    ".mcp.json": _MCP_JSON,
    ".gitignore": _GITIGNORE,
    ".github/workflows/ci.yml": _CI_WORKFLOW,
    "openapi.json": _OPENAPI_SNAPSHOT,
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
    "app/infra/sqlite_todo_repository.py": _SQLITE_REPOSITORY,
    "app/server/__init__.py": "",
    "app/server/asgi.py": _SERVER_APP,
    "app/server/context.py": _CONTEXT,
    "app/server/routes.py": _SERVER_ROUTES,
    "app/shared/__init__.py": "",
    "app/shared/errors.py": _SHARED_ERRORS,
    "tests/test_http.py": _HTTP_TEST,
    "tests/test_openapi_snapshot.py": _OPENAPI_TEST,
}


def app_files(app_name: str) -> dict[str, str]:
    """Return the scaffold as a mapping of relative path to file content."""
    return {
        path: content.replace("__APP_NAME__", app_name)
        .replace("__APP_ENV_PREFIX__", app_name.upper())
        .replace(
            "__UNSUPPORTED_MEDIA_TYPE_DESCRIPTION__",
            "UNSUPPORTED_MEDIA_TYPE: Request media type does not match the contract",
        )
        for path, content in _FILES.items()
    }


_MAKE_FEATURE_ROUTES = '''\
"""Routes for the __FEATURE__ feature.

Bind contracts to use cases with route(contract, use_case), then compose
this group in app/server/routes.py. Boundary and return annotations must
exactly match the contract types.
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
        "policy.py": (
            f'"""Authorization rules for the {feature} feature.\n'
            "\n"
            "An ability lives in the feature that owns the subject it\n"
            "inspects. Policies take their subjects as arguments and raise\n"
            'AppError; use cases fetch, then ask.\n"""\n'
        ),
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
