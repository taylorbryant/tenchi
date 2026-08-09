import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

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


def _assert_operation_error(
    result: subprocess.CompletedProcess[str],
    *,
    operation: str,
    code: str,
) -> dict[str, Any]:
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": 8,
        "result": "operation_error",
        "operation": operation,
        "ok": False,
        "code": code,
        "message": payload["message"],
        "details": payload["details"],
    }
    return payload


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


def _write_job_module(
    root: Path,
    *,
    required: bool = False,
    description: str = "Deliver mail.",
) -> None:
    priority = "priority: int" if required else "priority: int | None = None"
    (root / "job_app.py").write_text(
        "from pydantic import BaseModel\n"
        "from tenchi.jobs import job, job_group, job_handler\n"
        "\n"
        "class Delivery(BaseModel):\n"
        "    message_id: str\n"
        f"    {priority}\n"
        "\n"
        "async def deliver(request: Delivery, context: object) -> None:\n"
        "    del request, context\n"
        "\n"
        "declared = job(\n"
        '    "mail.deliver",\n'
        "    request=Delivery,\n"
        "    result=None,\n"
        f"    description={description!r},\n"
        ")\n"
        "jobs = job_group(job_handler(declared, deliver))\n",
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
    assert (root / "jobs.json").is_file()
    assert (root / "tools.json").is_file()
    assert (root / "evaluations.json").is_file()
    assert tomllib.loads((root / "tenchi.toml").read_text()) == {
        "schema_version": 1,
        "verify": {
            "check": True,
            "architecture": True,
            "openapi": True,
            "jobs": True,
            "tools": True,
            "evaluations": True,
        },
    }
    assert (root / "AGENTS.md").is_file()
    assert (root / ".mcp.json").is_file()
    assert (root / "tests/test_openapi_snapshot.py").is_file()
    assert (root / "tests/test_job_snapshot.py").is_file()
    assert (root / "tests/test_tool_snapshot.py").is_file()
    assert (root / "tests/test_evaluation_snapshot.py").is_file()
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
    assert app_map["schema_version"] == 8
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
        "tests": 6,
        "diagnostics": 0,
        "unresolved": 0,
    }

    preflight = _tenchi(root, "preflight", "--json")
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    preflight_result = json.loads(preflight.stdout)
    assert preflight_result["schema_version"] == 8
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
    assert listed_evaluations["schema_version"] == 8
    assert listed_evaluations["evaluations"] == []

    evaluation_run = _tenchi(root, "eval", "run", "--json")
    assert evaluation_run.returncode == 0, evaluation_run.stdout + evaluation_run.stderr
    evaluation_report = json.loads(evaluation_run.stdout)
    assert evaluation_report["schema_version"] == 8
    assert evaluation_report["ok"] is True
    assert evaluation_report["counts"] == {
        "completed": 0,
        "failed": 0,
        "timed_out": 0,
        "skipped": 0,
        "total": 0,
    }


def test_machine_readable_commands_keep_application_output_out_of_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    routes_path = root / "app/server/routes.py"
    routes_path.write_text(
        'print("route import output")\n' + routes_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    evaluations_path = root / "app/server/evaluations.py"
    evaluations_path.write_text(
        'print("private evaluation output")\n'
        + evaluations_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    for command in (("routes", "--json"), ("map", "--json"), ("openapi",)):
        result = _tenchi(root, *command)

        assert result.returncode == 0, result.stdout + result.stderr
        json.loads(result.stdout)
        assert "route import output" not in result.stdout
        assert "private evaluation output" not in result.stdout + result.stderr


def test_map_json_keeps_mapping_output_out_of_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import tenchi.cli as cli

    monkeypatch.chdir(EXAMPLE_DIR)
    original_map_app = cli.map_app  # pyright: ignore[reportPrivateImportUsage]

    def noisy_map_app(*args: Any, **kwargs: Any) -> Any:
        print("application map output")
        return original_map_app(*args, **kwargs)

    monkeypatch.setattr(cli, "map_app", noisy_map_app)

    assert main(["map", "--json"]) == 0

    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "application map output" not in captured.out
    assert "application map output" in captured.err


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


def test_eval_snapshot_cli_writes_checks_and_classifies_policy_changes(
    tmp_path: Path,
) -> None:
    _write_evaluation_module(tmp_path)
    target = "evaluation_app:runner"
    snapshot = tmp_path / "evaluations.json"

    printed = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
    )
    written = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--write",
        "evaluations.json",
    )
    checked = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--check",
        "evaluations.json",
    )

    assert printed.returncode == 0, printed.stderr
    manifest = json.loads(printed.stdout)
    assert manifest["schema_version"] == 1
    assert manifest["evaluations"][0]["cases"] == ["answers.quality.simple"]
    assert "private prompt" not in printed.stdout + printed.stderr
    assert written.returncode == 0, written.stderr
    assert checked.returncode == 0, checked.stderr
    assert json.loads(snapshot.read_text()) == manifest

    module = tmp_path / "evaluation_app.py"
    module.write_text(module.read_text().replace("threshold=0.8", "threshold=0.50"))
    changed = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--diff",
        "evaluations.json",
        "--diff-format",
        "json",
    )

    assert changed.returncode == 1
    report = json.loads(changed.stdout)
    assert report["schema_version"] == 8
    assert report["compatible"] is False
    assert report["changes"][0]["message"].startswith("threshold decreased")
    assert "private prompt" not in changed.stdout + changed.stderr

    snapshot.write_text("1" * 5000, encoding="utf-8")
    invalid = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--diff",
        "evaluations.json",
    )

    assert invalid.returncode == 1
    assert "is not valid JSON" in invalid.stderr
    assert "Traceback" not in invalid.stderr


def test_eval_snapshot_diff_ref_requires_an_explicit_missing_baseline_override(
    tmp_path: Path,
) -> None:
    _write_evaluation_module(tmp_path)
    target = "evaluation_app:runner"
    written = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--write",
        "evaluations.json",
    )
    assert written.returncode == 0, written.stdout + written.stderr
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "evaluation_app.py", "evaluations.json")
    _git(tmp_path, "commit", "-qm", "application with the original policy path")

    rejected = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--diff-ref",
        "HEAD",
        "--snapshot",
        "policy/evaluations.json",
        "--diff-format",
        "json",
    )

    failure = _assert_operation_error(
        rejected,
        operation="eval.snapshot",
        code="TENCHI_CLI_OPERATION_FAILED",
    )
    assert failure["message"] == "Could not compare the evaluation-policy baseline."
    assert rejected.stderr == ""

    allowed = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        target,
        "--diff-ref",
        "HEAD",
        "--snapshot",
        "policy/evaluations.json",
        "--allow-missing-baseline",
        "--diff-format",
        "json",
    )

    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    report = json.loads(allowed.stdout)
    assert report["compatible"] is True
    assert report["counts"]["additive"] == 1
    assert report["counts"]["metadata"] == 1
    assert report["baseline"].endswith(":policy/evaluations.json")
    assert {
        "severity": "metadata",
        "location": "evaluation manifest baseline",
        "message": (
            "historical baseline absent; explicit first-adoption override used"
        ),
    } in report["changes"]


def test_eval_snapshot_missing_baseline_override_requires_a_git_diff(
    tmp_path: Path,
) -> None:
    _write_evaluation_module(tmp_path)

    result = _tenchi(
        tmp_path,
        "eval",
        "snapshot",
        "--evaluations",
        "evaluation_app:runner",
        "--allow-missing-baseline",
    )

    assert result.returncode == 1
    assert "--allow-missing-baseline requires --diff-ref" in result.stderr


def test_jobs_cli_writes_checks_and_classifies_snapshots(tmp_path: Path) -> None:
    _write_job_module(tmp_path)
    target = "job_app:jobs"

    listed = _tenchi(tmp_path, "jobs", "--jobs", target)
    assert listed.returncode == 0, listed.stderr
    manifest = json.loads(listed.stdout)
    assert manifest["schema_version"] == 1
    assert [item["name"] for item in manifest["jobs"]] == ["mail.deliver"]

    agent_result = _tenchi(tmp_path, "jobs", "--jobs", target, "--json")
    assert agent_result.returncode == 0, agent_result.stderr
    listed_result = json.loads(agent_result.stdout)
    assert listed_result["schema_version"] == 8
    assert listed_result["manifest"] == manifest

    written = _tenchi(tmp_path, "jobs", "--jobs", target, "--write", "jobs.json")
    assert written.returncode == 0, written.stderr
    checked = _tenchi(tmp_path, "jobs", "--jobs", target, "--check", "jobs.json")
    assert checked.returncode == 0, checked.stderr

    _write_job_module(tmp_path, required=True, description="Deliver queued mail.")
    diff = _tenchi(
        tmp_path,
        "jobs",
        "--jobs",
        target,
        "--diff",
        "jobs.json",
        "--diff-format",
        "json",
    )
    assert diff.returncode == 1
    report = json.loads(diff.stdout)
    assert report["status"] == "incompatible"
    assert report["counts"]["breaking"] >= 1
    assert report["counts"]["metadata"] == 1

    drift = _tenchi(tmp_path, "jobs", "--jobs", target, "--check", "jobs.json")
    assert drift.returncode == 1
    assert "[breaking]" in drift.stderr
    assert "generated jobs" in drift.stderr


def test_jobs_diff_ref_requires_explicit_first_adoption(tmp_path: Path) -> None:
    _write_job_module(tmp_path)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "job_app.py")
    _git(tmp_path, "commit", "-qm", "before job snapshots")

    rejected = _tenchi(
        tmp_path,
        "jobs",
        "--jobs",
        "job_app:jobs",
        "--diff-ref",
        "HEAD",
        "--diff-format",
        "json",
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["operation"] == "jobs"

    allowed = _tenchi(
        tmp_path,
        "jobs",
        "--jobs",
        "job_app:jobs",
        "--diff-ref",
        "HEAD",
        "--allow-missing-baseline",
        "--diff-format",
        "json",
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    report = json.loads(allowed.stdout)
    assert report["counts"]["additive"] == 1
    assert report["counts"]["metadata"] == 1


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
    assert listed_result["schema_version"] == 8
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
    assert report["schema_version"] == 8
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
    invalid_diff = json.loads(capsys.readouterr().out)
    assert invalid_diff["operation"] == "tools"
    assert invalid_diff["code"] == "TENCHI_CLI_INVALID_ARGUMENTS"
    assert "--diff-format requires --diff" in invalid_diff["message"]

    assert main(["tools", "--snapshot", "tools.json"]) == 1
    assert "--snapshot requires --diff-ref" in capsys.readouterr().err

    assert main(["tools", "--json", "--check", "tools.json"]) == 1
    incompatible = json.loads(capsys.readouterr().out)
    assert incompatible["operation"] == "tools"
    assert incompatible["code"] == "TENCHI_CLI_INVALID_ARGUMENTS"
    assert "--json cannot be combined" in incompatible["message"]


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
    assert payload["schema_version"] == 8
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

    payload = _assert_operation_error(
        result,
        operation="preflight",
        code="TENCHI_CLI_TARGET_LOAD_FAILED",
    )
    assert payload["details"] == {"target": "environment:checks"}
    assert "secret-manager-token" not in result.stdout
    assert "secret-manager-token" not in result.stderr


@pytest.mark.parametrize(
    ("arguments", "operation"),
    [
        (("routes", "--routes", "missing:routes", "--json"), "routes"),
        (("tools", "--tools", "missing:tools", "--json"), "tools"),
        (("map", "--routes", "missing:routes", "--json"), "map"),
        (
            ("preflight", "--preflight", "missing:checks", "--json"),
            "preflight",
        ),
        (
            ("eval", "list", "--evaluations", "missing:runner", "--json"),
            "eval.list",
        ),
        (
            ("eval", "run", "--evaluations", "missing:runner", "--json"),
            "eval.run",
        ),
        (("task", "list", "--tasks", "missing:runner", "--json"), "task.list"),
        (
            ("task", "run", "repair", "--tasks", "missing:runner", "--json"),
            "task.run",
        ),
    ],
)
def test_json_commands_report_target_loading_failures(
    tmp_path: Path,
    arguments: tuple[str, ...],
    operation: str,
) -> None:
    result = _tenchi(tmp_path, *arguments)

    payload = _assert_operation_error(
        result,
        operation=operation,
        code="TENCHI_CLI_TARGET_LOAD_FAILED",
    )
    assert payload["message"].startswith("Could not load")
    assert result.stderr == ""


def test_json_argument_errors_are_versioned_and_do_not_echo_values(
    tmp_path: Path,
) -> None:
    result = _tenchi(
        tmp_path,
        "routes",
        "--json",
        "--unknown-option",
        "application-secret",
    )

    assert result.returncode == 2
    payload = _assert_operation_error(
        result,
        operation="routes",
        code="TENCHI_CLI_INVALID_ARGUMENTS",
    )
    assert payload["message"] == "Command arguments are invalid."
    assert payload["details"] is None
    assert "application-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("arguments", "operation"),
    [
        (("routes", "--json", "--help"), "routes"),
        (("openapi", "--diff-format", "json", "--help"), "openapi"),
    ],
)
def test_structured_json_cannot_be_bypassed_by_help(
    tmp_path: Path,
    arguments: tuple[str, ...],
    operation: str,
) -> None:
    result = _tenchi(tmp_path, *arguments)

    payload = _assert_operation_error(
        result,
        operation=operation,
        code="TENCHI_CLI_INVALID_ARGUMENTS",
    )
    assert payload["message"] == (
        "Structured JSON output cannot be combined with help."
    )
    assert payload["details"] is None
    assert result.stderr == ""


def test_json_operation_errors_redact_application_exceptions(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text(
        "raise RuntimeError('application-secret')\n",
        encoding="utf-8",
    )

    result = _tenchi(
        tmp_path,
        "routes",
        "--routes",
        "broken:routes",
        "--json",
    )

    _assert_operation_error(
        result,
        operation="routes",
        code="TENCHI_CLI_TARGET_LOAD_FAILED",
    )
    assert "application-secret" not in result.stdout + result.stderr


def test_map_json_reports_an_unknown_feature_as_a_stable_selection_failure() -> None:
    result = _tenchi(EXAMPLE_DIR, "map", "--feature", "missing", "--json")

    payload = _assert_operation_error(
        result,
        operation="map",
        code="TENCHI_CLI_SELECTION_NOT_FOUND",
    )
    assert payload["details"] == {
        "feature": "missing",
        "available_features": ["todos"],
    }


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
    assert listing["schema_version"] == 8
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
    assert report["schema_version"] == 8
    assert report["ok"] is True
    assert report["counts"] == {"passed": 9, "failed": 0, "total": 9}
    assert [step["name"] for step in report["steps"]] == [
        "ruff format",
        "ruff",
        "pyright",
        "pytest",
        "doctor",
        "openapi",
        "evaluations",
        "jobs",
        "tools",
    ]


def test_verify_produces_one_receipt_against_an_immutable_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    evaluations_module = root / "app/server/evaluations.py"
    evaluations_module.write_text(
        evaluations_module.read_text(encoding="utf-8")
        + '\nprint("evaluation-import-secret")\n',
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline")
    commit = _git(root, "rev-parse", "HEAD").stdout.strip()

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["schema_version"] == 8
    assert receipt["tenchi_version"]
    assert receipt["ok"] is True
    assert receipt["baseline"] == {"ref": "HEAD", "commit": commit}
    assert receipt["policy"]["source"] == "repository"
    assert receipt["policy"]["baseline_source"] == "repository"
    assert receipt["policy"]["ok"] is True
    assert receipt["policy"]["compatible"] is True
    assert receipt["policy"]["changes"] == []
    assert receipt["policy"]["requirements"] == [
        {
            "stage": stage,
            "current": "required",
            "baseline": "required",
            "enforced": True,
            "status": "passed",
        }
        for stage in [
            "check",
            "architecture",
            "openapi",
            "jobs",
            "tools",
            "evaluations",
        ]
    ]
    assert receipt["check"]["ok"] is True
    assert receipt["architecture"]["ok"] is True
    assert receipt["architecture"]["diagnostics"] == []
    assert receipt["architecture"]["unresolved"] == []
    assert receipt["openapi"]["compatible"] is True
    assert receipt["openapi"]["baseline"].startswith(f"{commit}:")
    assert receipt["jobs"]["compatible"] is True
    assert receipt["jobs"]["baseline"].startswith(f"{commit}:")
    assert receipt["tools"]["compatible"] is True
    assert receipt["tools"]["baseline"].startswith(f"{commit}:")
    assert receipt["evaluations"]["compatible"] is True
    assert receipt["evaluations"]["baseline"].startswith(f"{commit}:")
    assert receipt["errors"] == []
    assert "evaluation-import-secret" not in result.stdout + result.stderr


def test_verify_requires_an_explicit_first_adoption_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline at the original policy path")
    policy = root / "policy"
    policy.mkdir()
    (policy / "evaluations.json").write_text(
        (root / "evaluations.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    rejected = _tenchi(
        root,
        "verify",
        "--base-ref",
        "HEAD",
        "--evaluation-snapshot",
        "policy/evaluations.json",
        "--json",
    )

    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    rejected_receipt = json.loads(rejected.stdout)
    assert rejected_receipt["evaluations"] is None
    assert rejected_receipt["errors"][0]["stage"] == "evaluations"
    assert "could not read baseline" in rejected_receipt["errors"][0]["message"]

    allowed = _tenchi(
        root,
        "verify",
        "--base-ref",
        "HEAD",
        "--evaluation-snapshot",
        "policy/evaluations.json",
        "--allow-missing-evaluation-baseline",
        "--json",
    )

    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    allowed_receipt = json.loads(allowed.stdout)
    assert allowed_receipt["ok"] is True
    assert allowed_receipt["evaluations"]["counts"]["metadata"] == 1
    assert allowed_receipt["evaluations"]["changes"][-1]["location"] == (
        "evaluation manifest baseline"
    )


def test_verify_rejects_and_still_runs_a_weakened_required_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "strict policy")
    policy = root / "tenchi.toml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "check = true",
            "check = false",
        ),
        encoding="utf-8",
    )

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["check"]["ok"] is True
    assert receipt["policy"]["ok"] is False
    assert receipt["policy"]["compatible"] is False
    assert receipt["policy"]["changes"] == [
        {
            "severity": "breaking",
            "stage": "check",
            "message": "required check evidence became disabled",
        }
    ]
    check = receipt["policy"]["requirements"][0]
    assert check == {
        "stage": "check",
        "current": "disabled",
        "baseline": "required",
        "enforced": True,
        "status": "passed",
    }


def test_verify_reports_a_repository_disabled_stage_as_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    policy = root / "tenchi.toml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "evaluations = true",
            "evaluations = false",
        ),
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "policy without historical evaluation diff")

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["evaluations"] is None
    evaluation = receipt["policy"]["requirements"][-1]
    assert evaluation == {
        "stage": "evaluations",
        "current": "disabled",
        "baseline": "disabled",
        "enforced": False,
        "status": "skipped",
    }


def test_verify_runs_strict_defaults_when_the_current_policy_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "valid policy")
    (root / "tenchi.toml").write_text(
        'schema_version = 1\n[verify]\ncheck = "disabled"\n',
        encoding="utf-8",
    )

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["policy"] is None
    assert receipt["check"]["ok"] is True
    assert receipt["architecture"]["ok"] is True
    assert receipt["openapi"]["compatible"] is True
    assert receipt["jobs"]["compatible"] is True
    assert receipt["tools"]["compatible"] is True
    assert receipt["evaluations"]["compatible"] is True
    assert receipt["errors"] == [
        {
            "stage": "policy",
            "message": (
                "verification policy 'tenchi.toml' stage 'check' must be a boolean"
            ),
        }
    ]


def test_verify_rejects_a_policy_mutated_by_project_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "strict policy")
    (root / "tests/test_policy_mutation.py").write_text(
        """\
from pathlib import Path


def test_project_check_cannot_change_verification_policy() -> None:
    policy = Path(__file__).parents[1] / "tenchi.toml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "check = true",
            "check = false",
        ),
        encoding="utf-8",
    )
""",
        encoding="utf-8",
    )

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["check"]["ok"] is True
    assert receipt["policy"]["compatible"] is False
    assert receipt["errors"] == [
        {
            "stage": "policy",
            "message": (
                "verification policy changed while verification was running; "
                "rerun verify against the finished tree"
            ),
        }
    ]


def test_verify_requires_explicit_job_manifest_first_adoption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline at the original job path")
    policy = root / "policy"
    policy.mkdir()
    (policy / "jobs.json").write_text(
        (root / "jobs.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    rejected = _tenchi(
        root,
        "verify",
        "--base-ref",
        "HEAD",
        "--job-snapshot",
        "policy/jobs.json",
        "--json",
    )
    assert rejected.returncode == 1
    rejected_receipt = json.loads(rejected.stdout)
    assert rejected_receipt["jobs"] is None
    assert any(error["stage"] == "jobs" for error in rejected_receipt["errors"])

    allowed = _tenchi(
        root,
        "verify",
        "--base-ref",
        "HEAD",
        "--job-snapshot",
        "policy/jobs.json",
        "--allow-missing-job-baseline",
        "--json",
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    receipt = json.loads(allowed.stdout)
    assert receipt["jobs"]["counts"]["metadata"] == 1
    assert receipt["jobs"]["changes"][-1]["location"] == "job manifest baseline"


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


def test_verify_catches_a_weakened_evaluation_policy_after_snapshot_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    snapshot_path = root / "evaluations.json"
    current_snapshot = snapshot_path.read_text(encoding="utf-8")
    baseline = {
        "schema_version": 1,
        "evaluations": [
            {
                "name": "todos.answer_quality",
                "description": "Protect answer quality.",
                "kind": "model",
                "case_schema": {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
                "cases": ["todos.answer_quality.simple"],
                "metrics": [
                    {
                        "name": "quality",
                        "description": None,
                        "threshold": 0.8,
                    }
                ],
                "timeout_seconds": 30.0,
                "max_tokens": 100,
                "max_cost_usd": 0.01,
            }
        ],
    }
    snapshot_path.write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "baseline with evaluation policy")
    snapshot_path.write_text(current_snapshot, encoding="utf-8")

    result = _tenchi(root, "verify", "--base-ref", "HEAD", "--json")

    assert result.returncode == 1, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["check"]["ok"] is True
    assert receipt["architecture"]["ok"] is True
    assert receipt["openapi"]["compatible"] is True
    assert receipt["tools"]["compatible"] is True
    assert receipt["evaluations"]["compatible"] is False
    assert receipt["evaluations"]["counts"]["breaking"] == 1
    assert receipt["evaluations"]["changes"] == [
        {
            "severity": "breaking",
            "location": "evaluation 'todos.answer_quality'",
            "message": "evaluation removed",
        }
    ]
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
    assert receipt["jobs"] is None
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
    openapi_step = report["steps"][-4]
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
    assert compatible["schema_version"] == 8
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

    assert planned["schema_version"] == 8
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
    assert result["schema_version"] == 8
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
    import tenchi.cli as cli

    monkeypatch.chdir(EXAMPLE_DIR)
    original_openapi_schema = cli.openapi_schema  # pyright: ignore[reportPrivateImportUsage]

    def noisy_openapi_schema(*args: Any, **kwargs: Any) -> Any:
        print("schema generation output")
        return original_openapi_schema(*args, **kwargs)

    monkeypatch.setattr(cli, "openapi_schema", noisy_openapi_schema)

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
                "--routes",
                "app.server.routes:routes",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert "schema generation output" not in captured.out
    assert "schema generation output" in captured.err
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


@pytest.mark.parametrize(
    ("flag", "message"),
    [
        ("--title", "title must be a non-empty string"),
        ("--version", "version must be a non-empty string"),
    ],
)
def test_openapi_rejects_empty_metadata_overrides(
    flag: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", flag, ""]) == 1
    assert message in capsys.readouterr().err


def test_openapi_writes_file_and_discovers_route_metadata(
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
    assert document["info"] == {"title": "Todos", "version": "0.1.0"}
    assert set(document["paths"]) == {"/todos", "/todos/{todo_id}"}
    assert list(document) == sorted(document)
    assert output.read_text().endswith("\n")


def test_openapi_discovers_optional_description_and_security(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    routes_path = root / "app/server/routes.py"
    routes_path.write_text(
        routes_path.read_text().replace(
            "OPENAPI_DESCRIPTION: str | None = None",
            'OPENAPI_DESCRIPTION: str | None = "Generated API"\n'
            'OPENAPI_SECURITY = {"bearerAuth": {"type": "http", "scheme": "bearer"}}',
        )
    )

    result = _tenchi(root, "openapi")

    assert result.returncode == 0, result.stdout + result.stderr
    document = json.loads(result.stdout)
    assert document["info"]["description"] == "Generated API"
    assert document["security"] == [{"bearerAuth": []}]
    assert document["components"]["securitySchemes"] == {
        "bearerAuth": {"type": "http", "scheme": "bearer"}
    }


def test_openapi_output_remains_an_alias_for_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "openapi.json"
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--output", str(output)]) == 0

    assert output.is_file()


def test_bare_openapi_check_matches_the_application_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(EXAMPLE_DIR)

    assert main(["openapi", "--check", "openapi.json"]) == 0

    captured = capsys.readouterr()
    assert "OpenAPI snapshot matches openapi.json" in captured.out
    assert captured.err == ""


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


def test_openapi_json_diff_reports_a_versioned_baseline_failure(
    tmp_path: Path,
) -> None:
    result = _tenchi(
        EXAMPLE_DIR,
        "openapi",
        "--diff",
        str(tmp_path / "missing.json"),
        "--diff-format",
        "json",
    )

    payload = _assert_operation_error(
        result,
        operation="openapi",
        code="TENCHI_CLI_SNAPSHOT_READ_FAILED",
    )
    assert payload["message"] == "Could not read the OpenAPI baseline."
    assert payload["details"] == {"path": str(tmp_path / "missing.json")}
    assert result.stderr == ""


def test_openapi_json_diff_reports_invalid_configuration_without_details() -> None:
    result = _tenchi(
        EXAMPLE_DIR,
        "openapi",
        "--security",
        '{"auth":"invalid"}',
        "--diff",
        "missing.json",
        "--diff-format",
        "json",
    )

    payload = _assert_operation_error(
        result,
        operation="openapi",
        code="TENCHI_CLI_CONFIGURATION_INVALID",
    )
    assert payload["message"] == "The OpenAPI configuration is invalid."
    assert payload["details"] is None
    assert result.stderr == ""


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

    payload = json.loads(capsys.readouterr().out)
    assert payload["operation"] == "openapi"
    assert payload["code"] == "TENCHI_CLI_INVALID_ARGUMENTS"
    assert "--diff-format requires --diff" in payload["message"]
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
    assert result["schema_version"] == 8
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
