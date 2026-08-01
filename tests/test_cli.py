import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tenchi.cli import main
from tenchi.evaluations import MAX_EVALUATION_TOKENS

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


def _write_tool_module(
    root: Path,
    *,
    required: bool = False,
    description: str = "Search projects.",
) -> None:
    required_line = '        "required": ["query"],\n' if required else ""
    (root / "tool_app.py").write_text(
        "from typing import Annotated\n"
        "\n"
        "from pydantic import WithJsonSchema\n"
        "from tenchi.tools import tool, tool_group, tool_handler\n"
        "\n"
        "SearchInput = Annotated[\n"
        "    dict[str, str],\n"
        "    WithJsonSchema(\n"
        "        {\n"
        '            "type": "object",\n'
        '            "properties": {"query": {"type": "string"}},\n'
        f"{required_line}"
        '            "additionalProperties": False,\n'
        "        }\n"
        "    ),\n"
        "]\n"
        "\n"
        "async def search(request: SearchInput, context: object) -> str:\n"
        "    del context\n"
        '    return request.get("query", "")\n'
        "\n"
        "search_tool = tool(\n"
        '    "projects.search",\n'
        "    request=SearchInput,\n"
        "    result=str,\n"
        f"    description={description!r},\n"
        "    read_only=True,\n"
        ")\n"
        "tools = tool_group(tool_handler(search_tool, search))\n",
        encoding="utf-8",
    )


def _write_evaluation_module(
    root: Path,
    *,
    max_tokens: int = 10,
    tokens: int | None = 3,
) -> None:
    usage = "" if tokens is None else f", tokens={tokens}"
    (root / "evaluation_app.py").write_text(
        "from pydantic import BaseModel\n"
        "from tenchi.evaluations import (\n"
        "    EvaluationMeasurement,\n"
        "    create_evaluation_runner,\n"
        "    evaluation,\n"
        "    evaluation_case,\n"
        "    evaluation_group,\n"
        "    evaluation_metric,\n"
        "    evaluation_result,\n"
        ")\n"
        "class Case(BaseModel):\n"
        "    prompt: str\n"
        "async def score(case: Case, context: object) -> EvaluationMeasurement:\n"
        "    del context\n"
        "    print(case.prompt)\n"
        f"    return evaluation_result(scores={{'quality': 0.4}}{usage})\n"
        "suite = evaluation(\n"
        "    'answers.quality',\n"
        "    case=Case,\n"
        "    cases=(evaluation_case(\n"
        "        'answers.quality.simple', Case(prompt='private prompt')\n"
        "    ),),\n"
        "    metrics=(evaluation_metric('quality', threshold=0.8),),\n"
        "    evaluator=score,\n"
        "    kind='model',\n"
        f"    max_tokens={max_tokens},\n"
        ")\n"
        "runner = create_evaluation_runner(\n"
        "    evaluations=evaluation_group(suite),\n"
        "    context_factory=lambda: object(),\n"
        ")\n",
        encoding="utf-8",
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
    assert (root / "app/server/preflight.py").is_file()
    assert (root / "app/server/evaluations.py").is_file()
    assert (root / "app/server/runtime.py").is_file()
    assert (root / "app/server/tasks.py").is_file()
    assert (root / "app/server/tools.py").is_file()
    assert (root / "openapi.json").is_file()
    assert (root / "tools.json").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / ".mcp.json").is_file()
    assert (root / "tests/test_openapi_snapshot.py").is_file()
    assert (root / "tests/test_tool_snapshot.py").is_file()
    assert (root / ".github/workflows/ci.yml").is_file()
    assert "uv run tenchi check" in (root / "AGENTS.md").read_text()
    assert "https://tenchi.io/agents" in (root / "AGENTS.md").read_text()
    assert (
        "uv run tenchi map --feature <name> --json" in (root / "AGENTS.md").read_text()
    )
    assert "uv run tenchi map" in (root / "README.md").read_text()
    assert (
        "uv run tenchi verify --base-ref origin/main"
        in (root / "README.md").read_text()
    )
    assert "app.server.routes:api_routes" in (root / "AGENTS.md").read_text()
    assert "uv run tenchi check" in (root / ".github/workflows/ci.yml").read_text()
    assert "uv run tenchi verify" in (root / ".github/workflows/ci.yml").read_text()
    project_config = tomllib.loads((root / "pyproject.toml").read_text())
    assert project_config["project"]["dependencies"] == [
        "aiosqlite>=0.20",
        "tenchi",
    ]
    assert "tenchi[mcp]" in project_config["dependency-groups"]["dev"]
    mcp_config = json.loads((root / ".mcp.json").read_text())
    assert mcp_config["mcpServers"]["tenchi"] == {
        "command": "uv",
        "args": ["run", "tenchi", "mcp", "--root", "."],
    }

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

    mapped = _tenchi(root, "map", "--json")
    assert mapped.returncode == 0, mapped.stdout + mapped.stderr
    app_map = json.loads(mapped.stdout)
    assert app_map["schema_version"] == 5
    assert app_map["summary"] == {
        "features": 1,
        "contracts": 2,
        "routes": 2,
        "jobs": 0,
        "tasks": 0,
        "tools": 0,
        "evaluations": 0,
        "use_cases": 2,
        "policies": 0,
        "ports": 1,
        "adapters": 2,
        "contexts": 1,
        "entrypoints": 1,
        "tests": 4,
        "diagnostics": 0,
        "unresolved": 0,
    }

    preflight = _tenchi(root, "preflight", "--json")
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    preflight_result = json.loads(preflight.stdout)
    assert preflight_result["schema_version"] == 5
    assert preflight_result["ok"] is True
    assert preflight_result["counts"] == {
        "passed": 0,
        "failed": 0,
        "timed_out": 0,
        "total": 0,
    }

    evaluation_list = _tenchi(root, "eval", "list", "--json")
    assert evaluation_list.returncode == 0, (
        evaluation_list.stdout + evaluation_list.stderr
    )
    listed_evaluations = json.loads(evaluation_list.stdout)
    assert listed_evaluations["schema_version"] == 5
    assert listed_evaluations["evaluations"] == []

    evaluation_run = _tenchi(root, "eval", "run", "--json")
    assert evaluation_run.returncode == 0, evaluation_run.stdout + evaluation_run.stderr
    evaluation_report = json.loads(evaluation_run.stdout)
    assert evaluation_report["schema_version"] == 5
    assert evaluation_report["ok"] is True
    assert evaluation_report["counts"] == {
        "completed": 0,
        "failed": 0,
        "timed_out": 0,
        "skipped": 0,
        "total": 0,
    }


def test_new_with_a_long_valid_name_passes_generated_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    name = "customer_support_evaluation_service"
    monkeypatch.chdir(tmp_path)

    assert main(["new", name]) == 0
    capsys.readouterr()

    checked = _tenchi(tmp_path / name, "check")

    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_eval_cli_discovers_runs_and_redacts_application_payloads(
    tmp_path: Path,
) -> None:
    _write_evaluation_module(tmp_path)
    target = "evaluation_app:runner"

    listed = _tenchi(
        tmp_path,
        "eval",
        "list",
        "--evaluations",
        target,
        "--json",
    )
    ran = _tenchi(
        tmp_path,
        "eval",
        "run",
        "answers.quality",
        "--evaluations",
        target,
        "--json",
    )

    assert listed.returncode == 0, listed.stdout + listed.stderr
    listed_result = json.loads(listed.stdout)
    assert listed_result["evaluations"][0]["cases"] == ["answers.quality.simple"]
    assert "private prompt" not in listed.stdout + listed.stderr

    assert ran.returncode == 1, ran.stdout + ran.stderr
    run_result = json.loads(ran.stdout)
    assert run_result["ok"] is False
    assert run_result["evaluations"][0]["metrics"][0]["average"] == 0.4
    assert run_result["evaluations"][0]["metrics"][0]["passed"] is False
    assert "private prompt" not in ran.stdout + ran.stderr


def test_eval_cli_serializes_json_safe_token_limit(tmp_path: Path) -> None:
    _write_evaluation_module(
        tmp_path,
        max_tokens=MAX_EVALUATION_TOKENS,
        tokens=MAX_EVALUATION_TOKENS,
    )
    target = "evaluation_app:runner"

    listed = _tenchi(
        tmp_path,
        "eval",
        "list",
        "--evaluations",
        target,
        "--json",
    )
    ran = _tenchi(
        tmp_path,
        "eval",
        "run",
        "--evaluations",
        target,
        "--json",
    )

    assert listed.returncode == 0, listed.stdout + listed.stderr
    assert json.loads(listed.stdout)["evaluations"][0]["max_tokens"] == (
        MAX_EVALUATION_TOKENS
    )
    assert ran.returncode == 1, ran.stdout + ran.stderr
    run_result = json.loads(ran.stdout)
    outcome = run_result["evaluations"][0]
    assert outcome["cases"][0]["tokens"] == MAX_EVALUATION_TOKENS
    assert outcome["budget"]["consumed_tokens"] == MAX_EVALUATION_TOKENS
    assert outcome["budget"]["status"] == "passed"


def test_eval_cli_distinguishes_an_unverified_budget(tmp_path: Path) -> None:
    _write_evaluation_module(tmp_path, tokens=None)

    ran = _tenchi(
        tmp_path,
        "eval",
        "run",
        "--evaluations",
        "evaluation_app:runner",
    )

    assert ran.returncode == 1
    assert "declared token or cost budget could not be verified" in ran.stdout
    assert "declared token or cost budget exceeded" not in ran.stdout


def test_tools_cli_writes_checks_and_classifies_snapshots(tmp_path: Path) -> None:
    _write_tool_module(tmp_path)
    target = "tool_app:tools"

    listed = _tenchi(tmp_path, "tools", "--tools", target)
    assert listed.returncode == 0, listed.stderr
    manifest = json.loads(listed.stdout)
    assert manifest["schema_version"] == 1
    assert [tool["name"] for tool in manifest["tools"]] == ["projects.search"]

    agent_result = _tenchi(tmp_path, "tools", "--tools", target, "--json")
    assert agent_result.returncode == 0, agent_result.stderr
    listed_result = json.loads(agent_result.stdout)
    assert listed_result["schema_version"] == 5
    assert listed_result["root"] == str(tmp_path)
    assert listed_result["manifest"] == manifest

    written = _tenchi(
        tmp_path,
        "tools",
        "--tools",
        target,
        "--write",
        "tools.json",
    )
    assert written.returncode == 0, written.stderr
    checked = _tenchi(
        tmp_path,
        "tools",
        "--tools",
        target,
        "--check",
        "tools.json",
    )
    assert checked.returncode == 0, checked.stderr

    _write_tool_module(tmp_path, required=True, description="Find projects.")
    diff = _tenchi(
        tmp_path,
        "tools",
        "--tools",
        target,
        "--diff",
        "tools.json",
        "--diff-format",
        "json",
    )
    assert diff.returncode == 1
    report = json.loads(diff.stdout)
    assert report["schema_version"] == 5
    assert report["status"] == "incompatible"
    assert report["counts"]["breaking"] == 1
    assert report["counts"]["metadata"] == 1

    drift = _tenchi(
        tmp_path,
        "tools",
        "--tools",
        target,
        "--check",
        "tools.json",
    )
    assert drift.returncode == 1
    assert "[breaking]" in drift.stderr
    assert "property became required" in drift.stderr
    assert "generated tools" in drift.stderr


def test_tools_diff_ref_uses_the_historical_snapshot(tmp_path: Path) -> None:
    _write_tool_module(tmp_path)
    target = "tool_app:tools"
    snapshot = _tenchi(
        tmp_path,
        "tools",
        "--tools",
        target,
        "--write",
        "tools.json",
    )
    assert snapshot.returncode == 0, snapshot.stderr
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "tests@example.com")
    _git(tmp_path, "config", "user.name", "Tenchi tests")
    _git(tmp_path, "add", "tools.json")
    _git(tmp_path, "commit", "-m", "baseline")

    _write_tool_module(tmp_path, required=True)
    result = _tenchi(
        tmp_path,
        "tools",
        "--tools",
        target,
        "--diff-ref",
        "HEAD",
        "--snapshot",
        "tools.json",
    )

    assert result.returncode == 1
    assert "HEAD:tools.json" in result.stdout
    assert "property became required" in result.stdout


def test_tools_rejects_incompatible_output_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["tools", "--diff-format", "json"]) == 1
    assert "--diff-format requires --diff" in capsys.readouterr().err

    assert main(["tools", "--snapshot", "tools.json"]) == 1
    assert "--snapshot requires --diff-ref" in capsys.readouterr().err

    assert main(["tools", "--json", "--check", "tools.json"]) == 1
    assert "--json cannot be combined" in capsys.readouterr().err


def test_preflight_cli_returns_redacted_versioned_results(tmp_path: Path) -> None:
    (tmp_path / "environment.py").write_text(
        "from tenchi.preflight import preflight_check, preflight_group\n"
        "async def ready() -> None:\n"
        "    print('printed-database-password')\n"
        "    return None\n"
        "async def unavailable() -> None:\n"
        "    raise RuntimeError('database-password')\n"
        "checks = preflight_group(\n"
        "    preflight_check('database.connectivity', ready),\n"
        "    preflight_check(\n"
        "        'secrets.access',\n"
        "        unavailable,\n"
        "        description='Read the required secret reference.',\n"
        "        failure_code='SECRET_MANAGER_UNAVAILABLE',\n"
        "    ),\n"
        ")\n"
    )
    target = "environment:checks"

    result = _tenchi(
        tmp_path,
        "preflight",
        "--preflight",
        target,
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 5
    assert payload["target"] == target
    assert payload["ok"] is False
    assert payload["counts"] == {
        "passed": 1,
        "failed": 1,
        "timed_out": 0,
        "total": 2,
    }
    assert payload["checks"][1] == {
        "name": "secrets.access",
        "description": "Read the required secret reference.",
        "status": "failed",
        "duration_seconds": payload["checks"][1]["duration_seconds"],
        "failure_code": "SECRET_MANAGER_UNAVAILABLE",
    }
    assert "database-password" not in result.stdout
    assert "database-password" not in result.stderr
    assert "printed-database-password" not in result.stdout
    assert "printed-database-password" not in result.stderr


def test_preflight_cli_redacts_import_failures(tmp_path: Path) -> None:
    (tmp_path / "environment.py").write_text(
        "raise RuntimeError('secret-manager-token')\n"
    )

    result = _tenchi(
        tmp_path,
        "preflight",
        "--preflight",
        "environment:checks",
        "--json",
    )

    assert result.returncode == 1
    assert "could not import 'environment' (RuntimeError)" in result.stderr
    assert "secret-manager-token" not in result.stdout
    assert "secret-manager-token" not in result.stderr


def test_task_cli_lists_runs_and_reports_validation_as_versioned_json(
    tmp_path: Path,
) -> None:
    (tmp_path / "operations.py").write_text(
        "from pydantic import BaseModel, Field\n"
        "from tenchi.tasks import create_task_runner, task, task_group\n"
        "class Input(BaseModel):\n"
        "    count: int\n"
        "class Result(BaseModel):\n"
        "    completed: int = Field(serialization_alias='completedCount')\n"
        "async def repair(request: Input, context: object) -> Result:\n"
        "    print('application output')\n"
        "    return Result(completed=request.count)\n"
        "runner = create_task_runner(\n"
        "    tasks=task_group(task(\n"
        "        'records.repair', repair, description='Repair records.'\n"
        "    )),\n"
        "    context_factory=lambda: object(),\n"
        ")\n"
    )
    target = "operations:runner"

    listed = _tenchi(tmp_path, "task", "list", "--tasks", target, "--json")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    listing = json.loads(listed.stdout)
    assert listing["schema_version"] == 5
    assert listing["target"] == target
    assert listing["tasks"][0]["name"] == "records.repair"
    assert listing["tasks"][0]["input_required"] is True
    assert listing["tasks"][0]["input_schema"]["required"] == ["count"]
    assert listing["tasks"][0]["output_schema"]["required"] == ["completedCount"]

    ran = _tenchi(
        tmp_path,
        "task",
        "run",
        "records.repair",
        "--tasks",
        target,
        "--input",
        '{"count": 3}',
        "--json",
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    run_result = json.loads(ran.stdout)
    assert run_result["ok"] is True
    assert run_result["output"] == {"completedCount": 3}
    assert "application output" not in ran.stdout
    assert "application output" in ran.stderr

    invalid = _tenchi(
        tmp_path,
        "task",
        "run",
        "records.repair",
        "--tasks",
        target,
        "--input",
        '{"count": "no"}',
        "--json",
    )
    assert invalid.returncode == 1
    invalid_result = json.loads(invalid.stdout)
    assert invalid_result["error"]["kind"] == "invalid_input"
    assert "no" not in json.dumps(invalid_result["error"]["details"])


def test_generated_app_checks_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0

    result = _tenchi(tmp_path / "my_app", "check", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == 5
    assert report["ok"] is True
    assert report["counts"] == {"passed": 7, "failed": 0, "total": 7}
    assert [step["name"] for step in report["steps"]] == [
        "ruff format",
        "ruff",
        "pyright",
        "pytest",
        "doctor",
        "openapi",
        "tools",
    ]


def test_verify_produces_one_receipt_against_an_immutable_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    commit = _git(root, "rev-parse", "HEAD").stdout.strip()

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["schema_version"] == 5
    assert receipt["tenchi_version"]
    assert receipt["ok"] is True
    assert receipt["baseline"] == {"ref": "HEAD", "commit": commit}
    assert receipt["check"]["ok"] is True
    assert receipt["architecture"]["ok"] is True
    assert receipt["architecture"]["diagnostics"] == []
    assert receipt["architecture"]["unresolved"] == []
    assert receipt["openapi"]["compatible"] is True
    assert receipt["openapi"]["baseline"].startswith(f"{commit}:")
    assert receipt["tools"]["compatible"] is True
    assert receipt["tools"]["baseline"].startswith(f"{commit}:")
    assert receipt["errors"] == []


def test_verify_catches_a_breaking_snapshot_change_after_the_snapshot_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    snapshot_path = root / "openapi.json"
    current_snapshot = snapshot_path.read_text(encoding="utf-8")
    baseline = json.loads(current_snapshot)
    baseline["paths"]["/legacy"] = baseline["paths"]["/todos"]
    snapshot_path.write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline with legacy route")
    snapshot_path.write_text(current_snapshot, encoding="utf-8")

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["check"]["ok"] is True
    assert receipt["architecture"]["ok"] is True
    assert receipt["openapi"]["compatible"] is False
    assert receipt["openapi"]["counts"]["breaking"] >= 1
    assert receipt["tools"]["compatible"] is True
    assert receipt["ok"] is False


def test_verify_returns_a_structured_failure_for_an_unresolvable_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"

    result = _tenchi(root, "verify", "--base-ref", "missing", "--json")

    assert result.returncode == 1
    receipt = json.loads(result.stdout)
    assert receipt["ok"] is False
    assert receipt["baseline"] == {"ref": "missing", "commit": None}
    assert receipt["check"] is None
    assert receipt["architecture"] is None
    assert receipt["openapi"] is None
    assert receipt["tools"] is None
    assert receipt["errors"][0]["stage"] == "baseline"


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
    openapi_step = report["steps"][-2]
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
    assert compatible["schema_version"] == 5
    assert compatible["root"] == str(root)
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
        "tasks.py",
        "jobs.py",
        "tools.py",
        "use_cases/__init__.py",
        "tests/__init__.py",
    ):
        assert (feature_root / expected).is_file()

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.features.notes.routes import routes; "
            "from app.features.notes.tasks import tasks; "
            "from app.features.notes.tools import tools; "
            "from app.server.jobs import jobs; "
            "print(len(routes), len(tasks), len(tools), len(jobs))",
        ],
        cwd=tmp_path / "my_app",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0 0 0 0"


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

    assert planned["schema_version"] == 5
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["artifact"] == "feature"
    assert "app/features/notes/contracts.py" in planned["files"]
    assert "app/features/notes/tools.py" in planned["files"]
    assert any("app/server/tools.py" in step for step in planned["next_steps"])
    assert any("app/server/evaluations.py" in step for step in planned["next_steps"])
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
    assert result["schema_version"] == 5
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

    result = cast(dict[str, Any], json.loads(capsys.readouterr().out))
    assert result["schema_version"] == 5
    assert result["root"] == str(EXAMPLE_DIR)
    entries = cast(list[dict[str, Any]], result["routes"])
    assert entries
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
