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
            "from app.server.app import app; print(type(app).__name__)",
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


def test_routes_reports_missing_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["routes", "--routes", "nowhere.routes:routes"]) == 1
    assert "could not import" in capsys.readouterr().err


def test_routes_cli_entrypoint_runs_as_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tenchi.cli", "routes"],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "GET" in result.stdout
