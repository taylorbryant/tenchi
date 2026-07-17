import subprocess
import sys
from pathlib import Path

import pytest

from tenchi.cli import main

EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "todos"


def test_new_scaffolds_a_working_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["new", "my_app"]) == 0
    assert "Created my_app/" in capsys.readouterr().out

    root = tmp_path / "my_app"
    assert (
        (root / "pyproject.toml").read_text().startswith('[project]\nname = "my_app"')
    )
    assert (root / "app/features/todos/use_cases/create_todo.py").is_file()
    assert (root / "app/infra/port_wiring.py").is_file()
    assert (root / "app/shared/errors.py").is_file()

    # The generated app imports and composes an ASGI application using the
    # tenchi installed in this environment.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.server.asgi import app; print(type(app).__name__)",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Starlette"


def test_generated_app_tests_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path / "my_app",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_new_rejects_invalid_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["new", "MyApp"]) == 1
    assert main(["new", "1app"]) == 1
    assert "snake_case" in capsys.readouterr().err


def test_new_refuses_existing_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "my_app").mkdir()

    assert main(["new", "my_app"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_routes_prints_bound_routes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["routes"]) == 0

    out = capsys.readouterr().out
    assert "POST" in out
    assert "/todos/{todo_id}" in out
    assert "app.features.todos.use_cases.create_todo.create_todo" in out
    assert "TODO_NOT_FOUND" in out


def test_make_feature_scaffolds_importable_skeleton(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    monkeypatch.chdir(tmp_path / "my_app")

    assert main(["make", "feature", "notes"]) == 0
    out = capsys.readouterr().out
    assert "app/features/notes" in out
    assert "app/server/routes.py" in out

    feature_root = tmp_path / "my_app" / "app" / "features" / "notes"
    for expected in (
        "__init__.py",
        "schemas.py",
        "ports.py",
        "contracts.py",
        "policy.py",
        "routes.py",
        "use_cases/__init__.py",
        "tests/__init__.py",
    ):
        assert (feature_root / expected).is_file()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.features.notes.routes import routes; print(len(routes))",
        ],
        cwd=tmp_path / "my_app",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_make_feature_requires_app_root_and_refuses_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["make", "feature", "notes"]) == 1
    assert "app/features/ not found" in capsys.readouterr().err

    assert main(["new", "my_app"]) == 0
    monkeypatch.chdir(tmp_path / "my_app")
    assert main(["make", "feature", "todos"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_make_use_case_scaffolds_stub_and_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    monkeypatch.chdir(tmp_path / "my_app")
    assert main(["make", "feature", "notes"]) == 0

    assert main(["make", "use-case", "notes", "create_note"]) == 0
    out = capsys.readouterr().out
    assert "use_cases/create_note.py" in out

    feature_root = tmp_path / "my_app" / "app" / "features" / "notes"
    assert (feature_root / "use_cases/create_note.py").is_file()
    assert (feature_root / "tests/test_create_note.py").is_file()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.features.notes.use_cases.create_note import create_note",
        ],
        cwd=tmp_path / "my_app",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    # Generating again refuses to overwrite.
    assert main(["make", "use-case", "notes", "create_note"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_make_use_case_requires_existing_feature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    monkeypatch.chdir(tmp_path / "my_app")

    assert main(["make", "use-case", "missing", "create_note"]) == 1
    assert "tenchi make feature missing" in capsys.readouterr().err


def test_openapi_prints_document(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--title", "Todos", "--version", "9.9.9"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["info"] == {"title": "Todos", "version": "9.9.9"}
    assert "/todos" in document["paths"]


def test_openapi_writes_file_and_defaults_title_to_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    output = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--output", str(output)]) == 0
    assert "Wrote" in capsys.readouterr().out

    document = json.loads(output.read_text())
    assert document["info"]["title"] == EXAMPLE_DIR.name


def test_routes_reports_missing_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["routes", "--routes", "nowhere.routes:routes"]) == 1
    assert "could not import" in capsys.readouterr().err


def test_dev_serves_the_app(tmp_path: Path) -> None:
    import os
    import socket
    import time

    import httpx

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    env = dict(os.environ)
    env["TODOS_DATABASE"] = str(tmp_path / "todos.db")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tenchi.cli",
            "dev",
            "--port",
            str(port),
            "--no-reload",
        ],
        cwd=EXAMPLE_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        response = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/todos", timeout=1)
                break
            except httpx.TransportError:
                if process.poll() is not None:
                    break
                time.sleep(0.2)

        assert process.poll() is None, (
            process.stdout.read().decode() if process.stdout else "server exited"
        )
        assert response is not None and response.status_code == 200
        assert response.json() == []
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_routes_cli_entrypoint_runs_as_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tenchi.cli", "routes"],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "GET" in result.stdout


def test_generators_reject_python_keywords(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "class"]) == 1
    assert not (tmp_path / "class").exists()

    assert main(["new", "my_app"]) == 0
    monkeypatch.chdir(tmp_path / "my_app")
    assert main(["make", "feature", "import"]) == 1
    assert not (tmp_path / "my_app/app/features/import").exists()
    assert main(["make", "use-case", "todos", "return"]) == 1
    assert not (tmp_path / "my_app/app/features/todos/use_cases/return.py").exists()


def test_routes_json_emits_a_machine_readable_map(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    from typing import Any, cast

    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["routes", "--json"]) == 0

    entries = cast(list[dict[str, Any]], json.loads(capsys.readouterr().out))
    assert isinstance(entries, list) and entries
    create = next(e for e in entries if e["method"] == "POST" and e["path"] == "/todos")
    assert create["status"] == 201
    assert str(create["use_case"]).endswith("create_todo")
    assert create["response_headers"] == "CreatedTodoHeaders"
    assert "deprecated" in create and "sunset" in create
    assert create["successes"] == []
    assert create["timeout"] is None
