"""Execute documentation examples that protect identity and process health."""

import json
import re
import subprocess
import sys
from pathlib import Path

from tenchi.scaffold import app_files

CONTENT = Path(__file__).parents[1] / "docs" / "content"


def _blocks(page: str) -> list[str]:
    return re.findall(
        r"^```python\n(.*?)^```$",
        (CONTENT / page).read_text(),
        re.MULTILINE | re.DOTALL,
    )


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n")


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )


def _passes(root: Path, *args: str) -> str:
    result = _run(root, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_secure_todos_tutorial(tmp_path: Path) -> None:
    for name, content in app_files("my_app").items():
        _write(tmp_path, name, content)

    # Carry forward the feature tutorial's declarations and binding. The
    # security tutorial supplies complete replacements for the implementations.
    feature = _blocks("build-a-feature.mdx")
    params = next(block for block in feature if block.startswith("class CompleteTodo"))
    contract = next(
        block for block in feature if block.startswith("complete_todo_contract =")
    )
    schemas = tmp_path / "app/features/todos/schemas.py"
    schemas.write_text(schemas.read_text() + "\n\n" + params)
    contracts = tmp_path / "app/features/todos/contracts.py"
    contracts.write_text(
        contracts.read_text().replace(
            "from .schemas import CreateTodo, Todo",
            "from app.shared.errors import todo_not_found\n\n"
            "from .schemas import CompleteTodoParams, CreateTodo, Todo",
        )
        + "\n\n"
        + contract
    )
    routes = tmp_path / "app/features/todos/routes.py"
    routes.write_text(
        routes.read_text()
        .replace(
            "from .contracts import CreatedTodoHeaders,",
            "from .contracts import complete_todo_contract, CreatedTodoHeaders,",
        )
        .replace(
            "from .use_cases.create_todo import create_todo",
            "from .use_cases.complete_todo import complete_todo\n"
            "from .use_cases.create_todo import create_todo",
        )
        .replace(
            "    route(list_todos_contract, list_todos),",
            "    route(list_todos_contract, list_todos),\n"
            "    route(complete_todo_contract, complete_todo),",
        )
    )
    for block in _blocks("secure-todos.mdx"):
        header, _, content = block.partition("\n")
        assert header.startswith(("# app/", "# tests/"))
        _write(tmp_path, header.removeprefix("# "), content)

    # The tutorial deliberately changes authentication and tells readers to
    # review that incompatibility before accepting the new snapshot.
    diff = _run(
        tmp_path,
        "-m",
        "tenchi.cli",
        "openapi",
        "--diff",
        "openapi.json",
        "--diff-format",
        "json",
    )
    assert diff.returncode == 1, diff.stdout + diff.stderr
    report = json.loads(diff.stdout)
    assert report["compatible"] is False
    assert report["counts"]["breaking"] > 0
    _passes(tmp_path, "-m", "tenchi.cli", "openapi", "--write", "openapi.json")
    _passes(tmp_path, "-m", "ruff", "check", "--fix", ".")
    _passes(tmp_path, "-m", "ruff", "format", ".")
    _passes(tmp_path, "-m", "pyright", "--pythonpath", sys.executable)
    _passes(tmp_path, "-m", "tenchi.cli", "doctor")
    _passes(tmp_path, "-m", "pytest", "-q")


def test_documented_liveness_bypasses_context_and_preserves_lifespan(
    tmp_path: Path,
) -> None:
    deployment = next(
        block
        for block in _blocks("deployment.mdx")
        if block.startswith("# app/server/deployment.py")
    )
    for name in ("app/__init__.py", "app/server/__init__.py"):
        _write(tmp_path, name, "")
    _write(tmp_path, "app/server/deployment.py", deployment)
    _write(
        tmp_path,
        "app/server/asgi.py",
        """from contextlib import asynccontextmanager
from tenchi.health import health_route
from tenchi.routes import route_group
from tenchi.server import create_app

events = []

@asynccontextmanager
async def lifespan():
    events.append("startup")
    try:
        yield
    finally:
        events.append("shutdown")

async def create_context():
    events.append("database acquisition")
    raise RuntimeError("database unavailable")

app = create_app(
    routes=route_group(health_route(path="/ready")),
    context_factory=create_context,
    lifespan=lifespan,
)
""",
    )
    _passes(
        tmp_path,
        "-c",
        """import asyncio
from tenchi.testing import open_http
from app.server.asgi import events
from app.server.deployment import app

async def main():
    async with open_http(app) as http:
        assert events == ["startup"]
        live = await http.get("/live")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}
        assert events == ["startup"]
        ready = await http.get("/ready")
        assert ready.status_code == 500
        assert ready.json()["code"] == "INTERNAL_SERVER_ERROR"
        assert (await http.get("/live")).status_code == 200
        assert events == ["startup", "database acquisition"]
    assert events == ["startup", "database acquisition", "shutdown"]

asyncio.run(main())
""",
    )
