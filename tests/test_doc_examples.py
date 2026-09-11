"""Execute related documentation snippets together in isolated applications."""

import ast
import re
import subprocess
import sys
from pathlib import Path
from textwrap import indent

from tenchi.scaffold import app_files

CONTENT = Path(__file__).parents[1] / "docs" / "content"


def _block(page: str, contains: str) -> str:
    blocks = re.findall(
        r"^```python\n(.*?)^```$",
        (CONTENT / f"{page}.mdx").read_text(),
        re.MULTILINE | re.DOTALL,
    )
    matches = [block for block in blocks if contains in block]
    assert len(matches) == 1, (page, contains, len(matches))
    return matches[0]


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _app(root: Path) -> None:
    for relative, source in app_files("docs_example").items():
        _write(root, relative, source)
    for relative, page, marker in (
        ("features/todos/schemas.py", "contracts", "class CreateTodo("),
        ("features/todos/contracts.py", "contracts", "create_todo_contract ="),
        ("features/todos/routes.py", "server", "def create_todo_headers("),
        (
            "features/todos/use_cases/create_todo.py",
            "application",
            "async def create_todo(",
        ),
        (
            "infra/memory_todo_repository.py",
            "application",
            "class MemoryTodoRepository:",
        ),
    ):
        _write(root, f"app/{relative}", _block(page, marker))


def _run(root: Path, source: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_guides_compose_and_serve_the_same_models(tmp_path: Path) -> None:
    _app(tmp_path)
    _run(
        tmp_path,
        """
import asyncio
from tenchi.openapi import openapi_schema
from tenchi.server import create_app
from tenchi.testing import open_client
from app.features.todos.contracts import create_todo_contract
from app.features.todos.routes import routes
from app.features.todos.schemas import CreateTodo
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext

async def main():
    context = AppContext(todos=MemoryTodoRepository())
    app = create_app(routes=routes, context_factory=lambda: context)
    async with open_client(app) as client:
        response = await client.call_with_response(
            create_todo_contract, request=CreateTodo(title="Read the docs")
        )
    assert response.http_response.status_code == 201
    assert response.headers.location == f"/todos/{response.body.id}"
    assert await context.todos.get(response.body.id) == response.body
    schema = openapi_schema(routes, title="Docs", version="1.0.0")
    headers = schema["paths"]["/todos"]["post"]["responses"]["201"]["headers"]
    assert "Location" in headers

asyncio.run(main())
""",
    )


def test_response_guide_binds_path_inputs_and_selects_status(tmp_path: Path) -> None:
    _app(tmp_path)
    _write(
        tmp_path,
        "app/features/todos/routes.py",
        _block("responses", "class PutTodoResult:"),
    )
    _run(
        tmp_path,
        """
from app.features.todos.routes import (
    PutTodoParams, PutTodoResult, present_put, put_todo_route,
)
from app.features.todos.schemas import Todo

assert put_todo_route.contract.params is PutTodoParams
assert put_todo_route.call_kwargs == ("params", "request", "context")
todo = Todo(id="example", title="Read the docs", completed=False)
assert present_put(PutTodoResult(todo=todo, created=True)).definition.status == 201
assert present_put(PutTodoResult(todo=todo, created=False)).definition.status == 200
""",
    )


def test_idempotency_guide_client_sends_its_header_model(tmp_path: Path) -> None:
    declaration = _block("idempotency", "class CreateTaskHeaders(")
    client = _block("idempotency", "headers = CreateTaskHeaders(")
    _run(
        tmp_path,
        "CreateTask = str\nTask = str\n"
        + declaration
        + """
import asyncio
import httpx
from tenchi.client import Client

async def main():
    def respond(outbound):
        assert outbound.headers["idempotency-key"] == key
        return httpx.Response(201, json="created")
    async with Client(transport=httpx.MockTransport(respond)) as client:
        request = "example"
"""
        + indent(client, "        ")
        + '\n        assert task == "created"\n\nasyncio.run(main())\n',
    )


def test_http_input_adapter_reuses_the_tutorial_behavior(tmp_path: Path) -> None:
    _app(tmp_path)
    schemas = tmp_path / "app/features/todos/schemas.py"
    schemas.write_text(
        schemas.read_text()
        + "\n"
        + _block("build-a-feature", "class CompleteTodoParams(")
    )
    memory = tmp_path / "app/infra/memory_todo_repository.py"
    memory.write_text(
        memory.read_text()
        + "\n"
        + indent(_block("build-a-feature", "self._todos.get(todo_id)"), "    ")
    )
    _write(
        tmp_path,
        "app/features/todos/use_cases/complete_todo.py",
        _block("build-a-feature", "from tenchi.errors import AppError"),
    )
    _write(
        tmp_path,
        "app/features/todos/use_cases/complete_todo_from_request.py",
        _block("execution", "async def complete_todo_from_request("),
    )
    _write(
        tmp_path,
        "app/features/todos/tasks.py",
        _block("execution", 'task("todos.complete",'),
    )
    _run(
        tmp_path,
        """
import asyncio
from tenchi.execution import execute
from tenchi.tools import tool, tool_handler
from app.features.todos.schemas import CompleteTodoParams, Todo
from app.features.todos.tasks import tasks
from app.features.todos.use_cases.complete_todo_from_request import (
    complete_todo_from_request,
)
from app.infra.memory_todo_repository import MemoryTodoRepository
from app.server.context import AppContext

async def main():
    context = AppContext(todos=MemoryTodoRepository())
    todo = await context.todos.create(title="Read the docs")
    declaration = tool("todos.complete", request=CompleteTodoParams, result=Todo,
                       description="Complete a todo.")
    tool_handler(declaration, complete_todo_from_request)
    result = await execute(complete_todo_from_request,
                           request={"todo_id": todo.id}, context=context)
    assert result.id == todo.id
    assert result.completed is True
    assert (await context.todos.get(todo.id)).completed is True

asyncio.run(main())
""",
    )


def test_evaluation_guide_uses_exported_runtime_names(tmp_path: Path) -> None:
    _app(tmp_path)
    tree = ast.parse(_block("evaluations", "runner = create_evaluation_runner("))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.server.runtime"
    ]
    assert len(imports) == 1
    _run(tmp_path, ast.unparse(imports[0]))


def test_preflight_guide_checks_an_existing_database_read_only(tmp_path: Path) -> None:
    _app(tmp_path)
    _write(
        tmp_path,
        "app/infra/preflight_database.py",
        _block("preflight", "async def open_preflight_connection("),
    )
    _write(
        tmp_path,
        "app/server/preflight.py",
        _block("preflight", "async def database_connectivity("),
    )
    _run(
        tmp_path,
        """
import asyncio
import sqlite3
from tenchi.preflight import run_preflight
from app.infra.preflight_database import open_preflight_connection
from app.server import preflight

preflight.DATABASE_PATH = "preflight.db"
with sqlite3.connect(preflight.DATABASE_PATH) as connection:
    connection.execute("PRAGMA user_version = 7")

async def main():
    assert (await run_preflight(preflight.checks)).ok
    async with open_preflight_connection(preflight.DATABASE_PATH) as connection:
        try:
            await connection.execute("CREATE TABLE forbidden (id INTEGER)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("Preflight connection allowed a write")
    with sqlite3.connect(preflight.DATABASE_PATH) as connection:
        connection.execute("PRAGMA user_version = 6")
    assert not (await run_preflight(preflight.checks)).ok

asyncio.run(main())
""",
    )
