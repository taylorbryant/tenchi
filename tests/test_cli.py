import json
import subprocess
import sys
from pathlib import Path

import pytest

from tenchi.cli import main

EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "todos"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


def _tenchi(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tenchi.cli", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


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
    assert (root / "app/infra/sqlite_todo_repository.py").is_file()
    assert (root / "app/shared/errors.py").is_file()
    assert (root / "openapi.json").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "tests/test_openapi_snapshot.py").is_file()
    assert (root / ".github/workflows/ci.yml").is_file()
    assert "uv run tenchi check" in (root / "AGENTS.md").read_text()
    assert "app.server.routes:api_routes" in (root / "AGENTS.md").read_text()
    assert "uv run tenchi check" in (root / ".github/workflows/ci.yml").read_text()

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


def test_generated_app_checks_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0

    result = _tenchi(tmp_path / "my_app", "check", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 1
    assert report["ok"] is True
    assert report["counts"] == {"passed": 6, "failed": 0, "total": 6}
    assert [step["name"] for step in report["steps"]] == [
        "ruff format",
        "ruff",
        "pyright",
        "pytest",
        "doctor",
        "openapi",
    ]


def test_check_discovers_an_openapi_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    routes_path = root / "app/server/routes.py"
    routes_path.write_text(
        routes_path.read_text().replace(
            "OPENAPI_DESCRIPTION: str | None = None",
            'OPENAPI_DESCRIPTION: str | None = "Generated API"',
        )
    )
    written = _tenchi(
        root,
        "openapi",
        "--routes",
        "app.server.routes:api_routes",
        "--title",
        "my_app",
        "--version",
        "0.1.0",
        "--description",
        "Generated API",
        "--write",
        "openapi.json",
    )
    assert written.returncode == 0, written.stderr

    result = _tenchi(root, "check", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    openapi_step = report["steps"][-1]
    assert openapi_step["status"] == "passed"
    assert openapi_step["command"][8:10] == ["--description", "Generated API"]


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1"])
def test_check_rejects_non_finite_or_non_positive_timeouts(timeout: str) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["check", "--timeout", timeout])

    assert raised.value.code == 2


def test_openapi_diff_ref_reads_the_snapshot_from_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    capsys.readouterr()

    command = [
        "openapi",
        "--routes",
        "app.server.routes:api_routes",
        "--title",
        "my_app",
        "--version",
        "0.1.0",
    ]
    compatible_result = _tenchi(
        root, *command, "--diff-ref", "HEAD", "--diff-format", "json"
    )
    assert compatible_result.returncode == 0, compatible_result.stderr
    compatible = json.loads(compatible_result.stdout)
    assert compatible["baseline"] == "HEAD:openapi.json"
    assert compatible["compatible"] is True

    snapshot = json.loads((root / "openapi.json").read_text())
    snapshot["paths"]["/todos"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]["title"]["minLength"] = 0
    (root / "openapi.json").write_text(json.dumps(snapshot))
    _git(root, "add", "openapi.json")
    _git(root, "commit", "-qm", "looser baseline")

    breaking_result = _tenchi(root, *command, "--diff-ref", "HEAD")
    assert breaking_result.returncode == 1
    report = breaking_result.stdout
    assert "HEAD:openapi.json" in report
    assert "BREAKING" in report


def test_openapi_diff_ref_reports_git_and_path_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    capsys.readouterr()
    command = [
        "openapi",
        "--routes",
        "app.server.routes:api_routes",
        "--title",
        "my_app",
    ]

    missing = _tenchi(root, *command, "--diff-ref", "missing")
    assert missing.returncode == 1
    assert "could not resolve Git ref" in missing.stderr

    missing_snapshot = _tenchi(
        root, *command, "--diff-ref", "HEAD", "--snapshot", "missing.json"
    )
    assert missing_snapshot.returncode == 1
    assert "could not read baseline" in missing_snapshot.stderr

    (root / "invalid.json").write_text("not JSON")
    _git(root, "add", "invalid.json")
    _git(root, "commit", "-qm", "invalid baseline")
    invalid_snapshot = _tenchi(
        root, *command, "--diff-ref", "HEAD", "--snapshot", "invalid.json"
    )
    assert invalid_snapshot.returncode == 1
    assert "is not valid JSON" in invalid_snapshot.stderr

    outside = _tenchi(
        root,
        *command,
        "--diff-ref",
        "HEAD",
        "--snapshot",
        str(tmp_path.parent / "outside.json"),
    )
    assert outside.returncode == 1
    assert "must resolve inside" in outside.stderr

    snapshot_only = _tenchi(root, *command, "--snapshot", "openapi.json")
    assert snapshot_only.returncode == 1
    assert "--snapshot requires --diff-ref" in snapshot_only.stderr


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


def test_make_dry_run_and_json_share_a_versioned_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    capsys.readouterr()
    root = tmp_path / "my_app"
    monkeypatch.chdir(root)

    assert main(["make", "feature", "notes", "--dry-run", "--json"]) == 0
    planned = json.loads(capsys.readouterr().out)

    assert planned["schema_version"] == 1
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["artifact"] == "feature"
    assert "app/features/notes/contracts.py" in planned["files"]
    assert not (root / "app/features/notes").exists()

    assert main(["make", "feature", "notes", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["files"] == planned["files"]
    assert created["dry_run"] is False
    assert (root / "app/features/notes/contracts.py").is_file()

    assert (
        main(
            [
                "make",
                "use-case",
                "notes",
                "create_note",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    use_case = json.loads(capsys.readouterr().out)
    assert use_case["feature"] == "notes"
    assert use_case["files"] == [
        "app/features/notes/use_cases/create_note.py",
        "app/features/notes/tests/test_create_note.py",
    ]
    assert not (root / "app/features/notes/use_cases/create_note.py").exists()


def test_make_json_reports_errors_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["make", "feature", "notes", "--json"]) == 1

    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1
    assert result["ok"] is False
    assert result["files"] == []
    assert "app/features/ not found" in result["error"]


def test_make_json_rolls_back_a_partial_filesystem_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    monkeypatch.chdir(root)
    capsys.readouterr()

    original_replace = Path.replace
    replace_calls = 0

    def fail_second_replace(source: Path, target: Path) -> Path:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated filesystem failure")
        return original_replace(source, target)

    with monkeypatch.context() as failure:
        failure.setattr(Path, "replace", fail_second_replace)
        assert main(["make", "feature", "notes", "--json"]) == 1

    failed = json.loads(capsys.readouterr().out)
    assert failed["ok"] is False
    assert failed["files"] == []
    assert "could not create files" in failed["error"]
    assert not (root / "app/features/notes").exists()

    assert main(["make", "feature", "notes", "--json"]) == 0
    assert (root / "app/features/notes/contracts.py").is_file()


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

    assert (
        main(
            [
                "openapi",
                "--title",
                "Todos",
                "--version",
                "9.9.9",
                "--description",
                "Todo API",
                "--security",
                '{"bearerAuth":{"type":"http","scheme":"bearer"}}',
            ]
        )
        == 0
    )

    document = json.loads(capsys.readouterr().out)
    assert document["info"] == {
        "description": "Todo API",
        "title": "Todos",
        "version": "9.9.9",
    }
    assert document["security"] == [{"bearerAuth": []}]
    assert "security" not in document["paths"]["/todos"]["get"]
    assert document["paths"]["/health"]["get"]["security"] == []
    assert document["paths"]["/openapi.json"]["get"]["security"] == []
    assert "/todos" in document["paths"]


def test_openapi_rejects_invalid_security_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--security", "not-json"]) == 1
    assert "--security must be valid JSON" in capsys.readouterr().err

    assert main(["openapi", "--security", "[]"]) == 1
    assert "--security must be a JSON object" in capsys.readouterr().err

    assert main(["openapi", "--security", '{"bearerAuth":"invalid"}']) == 1
    assert "security scheme 'bearerAuth' must be a mapping" in capsys.readouterr().err


def test_openapi_writes_file_and_defaults_title_to_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    output = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--write", str(output)]) == 0
    assert "Wrote" in capsys.readouterr().out

    document = json.loads(output.read_text())
    assert document["info"]["title"] == EXAMPLE_DIR.name
    assert list(document) == sorted(document)
    assert output.read_text().endswith("\n")


def test_openapi_output_remains_an_alias_for_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--output", str(output)]) == 0

    assert output.is_file()


def test_openapi_check_accepts_a_current_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)
    command = [
        "openapi",
        "--title",
        "Todos",
        "--version",
        "1.2.3",
        "--security",
        '{"bearerAuth":{"type":"http","scheme":"bearer"}}',
    ]

    assert main([*command, "--write", str(snapshot)]) == 0
    capsys.readouterr()

    assert main([*command, "--check", str(snapshot)]) == 0
    captured = capsys.readouterr()
    assert f"OpenAPI snapshot matches {snapshot}" in captured.out
    assert captured.err == ""


def test_openapi_check_describes_drift_and_shows_a_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    from typing import Any, cast

    snapshot = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)
    command = [
        "openapi",
        "--title",
        "Todos",
        "--version",
        "1.2.3",
        "--security",
        '{"bearerAuth":{"type":"http","scheme":"bearer"}}',
    ]
    assert main([*command, "--write", str(snapshot)]) == 0
    capsys.readouterr()

    stored = cast(dict[str, Any], json.loads(snapshot.read_text()))
    stored["info"]["version"] = "outdated"
    stored["components"]["securitySchemes"]["bearerAuth"]["scheme"] = "basic"
    del stored["paths"]["/todos/{todo_id}"]
    snapshot.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n")

    assert main([*command, "--check", str(snapshot)]) == 1

    error = capsys.readouterr().err
    assert f"snapshot differs: {snapshot}" in error
    assert "API metadata changed" in error
    assert "security schemes changed" in error
    assert "operation added: GET /todos/{todo_id}" in error
    assert f"--- {snapshot}" in error
    assert "+++ generated OpenAPI" in error
    assert "instead of --check to accept this change" in error


def test_openapi_check_reports_missing_and_invalid_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--check", str(snapshot)]) == 1
    missing_error = capsys.readouterr().err
    assert "could not read snapshot" in missing_error
    assert f"--write {snapshot} instead of --check" in missing_error

    snapshot.write_text("{not JSON}\n")

    assert main(["openapi", "--check", str(snapshot)]) == 1
    invalid_error = capsys.readouterr().err
    assert "stored snapshot is not valid JSON" in invalid_error
    assert "+++ generated OpenAPI" in invalid_error

    snapshot.write_bytes(b"\xff")

    assert main(["openapi", "--check", str(snapshot)]) == 1
    assert "could not read snapshot" in capsys.readouterr().err


def test_openapi_write_reports_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--write", str(tmp_path)]) == 1
    assert "could not write snapshot" in capsys.readouterr().err


def test_openapi_diff_accepts_an_identical_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)
    command = ["openapi", "--title", "Todos", "--version", "1.2.3"]
    assert main([*command, "--write", str(baseline)]) == 0
    capsys.readouterr()

    assert main([*command, "--diff", str(baseline)]) == 0

    output = capsys.readouterr().out
    assert f"against {baseline}: compatible" in output
    assert "No API changes found." in output


def test_openapi_diff_allows_additive_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    from typing import Any, cast

    baseline = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)
    command = ["openapi", "--title", "Todos", "--version", "1.2.3"]
    assert main([*command, "--write", str(baseline)]) == 0
    capsys.readouterr()
    stored = cast(dict[str, Any], json.loads(baseline.read_text()))
    del stored["paths"]["/todos/{todo_id}"]
    baseline.write_text(json.dumps(stored))

    assert main([*command, "--diff", str(baseline)]) == 0

    output = capsys.readouterr().out
    assert "compatible" in output
    assert "ADDITIVE" in output
    assert "operation added" in output


def test_openapi_diff_fails_for_breaking_changes_and_emits_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    from typing import Any, cast

    baseline = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)
    command = ["openapi", "--title", "Todos", "--version", "1.2.3"]
    assert main([*command, "--write", str(baseline)]) == 0
    capsys.readouterr()
    stored = cast(dict[str, Any], json.loads(baseline.read_text()))
    stored["paths"]["/legacy"] = {
        "get": {
            "operationId": "legacy",
            "responses": {"200": {"description": "Legacy"}},
        }
    }
    baseline.write_text(json.dumps(stored))

    assert (
        main(
            [
                *command,
                "--diff",
                str(baseline),
                "--diff-format",
                "json",
            ]
        )
        == 1
    )

    report = json.loads(capsys.readouterr().out)
    assert report["baseline"] == str(baseline)
    assert report["status"] == "incompatible"
    assert report["compatible"] is False
    assert report["counts"]["breaking"] == 1
    assert report["changes"][0]["message"] == "operation removed"


def test_openapi_diff_fails_closed_for_unknown_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json
    from typing import Any, cast

    baseline = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)
    command = ["openapi", "--title", "Todos", "--version", "1.2.3"]
    assert main([*command, "--write", str(baseline)]) == 0
    capsys.readouterr()
    stored = cast(dict[str, Any], json.loads(baseline.read_text()))
    stored["paths"]["/todos"]["get"]["x-unsupported"] = True
    baseline.write_text(json.dumps(stored))

    assert main([*command, "--diff", str(baseline)]) == 1

    output = capsys.readouterr().out
    assert "review required" in output
    assert "UNKNOWN" in output
    assert "unsupported operation fields changed" in output


def test_openapi_diff_reports_unreadable_and_invalid_baselines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--diff", str(baseline)]) == 1
    assert "could not read baseline" in capsys.readouterr().err

    baseline.write_text("not JSON")
    assert main(["openapi", "--diff", str(baseline)]) == 1
    invalid_error = capsys.readouterr().err
    assert "baseline" in invalid_error
    assert "is not valid JSON" in invalid_error

    baseline.write_text("{}")
    assert main(["openapi", "--diff", str(baseline)]) == 1
    assert "could not compare baseline" in capsys.readouterr().err


def test_openapi_diff_format_cannot_be_used_with_another_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert (
        main(
            [
                "openapi",
                "--write",
                str(snapshot),
                "--diff-format",
                "json",
            ]
        )
        == 1
    )

    assert "--diff-format requires --diff" in capsys.readouterr().err
    assert not snapshot.exists()


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
    assert create["responses"] == []
    assert create["timeout"] is None
    assert create["public"] is False
    health = next(e for e in entries if e["path"] == "/health")
    assert health["public"] is True
