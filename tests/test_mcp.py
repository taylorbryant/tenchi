import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import sleep
from types import ModuleType
from typing import cast

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ResourceError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent, TextResourceContents
from pydantic import AnyUrl

from tenchi import __version__, _mcp_server, _openapi_operations
from tenchi._checks import CheckCancelled
from tenchi._cli_results import CheckResult
from tenchi._mcp_server import McpServerOptions, build_mcp_server
from tenchi._verify_operations import VerificationErrorResult, VerificationResult

EXAMPLE_ROOT = Path(__file__).parent.parent / "examples" / "todos"


def test_project_reload_preserves_modules_from_the_active_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = tmp_path / ".venv"
    module = ModuleType("mcp_dependency_sentinel")
    module.__file__ = str(environment / "site-packages/sentinel/__init__.py")
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setitem(sys.modules, module.__name__, module)

    with _openapi_operations.isolated_project_imports(
        tmp_path, module_names=("app.server.routes",)
    ):
        assert sys.modules[module.__name__] is module

    assert sys.modules[module.__name__] is module


def test_stdio_runner_preserves_every_captured_application_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = McpServerOptions(
        root=tmp_path,
        routes="custom.routes:routes",
        api_routes="custom.routes:api_routes",
        preflight="custom.preflight:checks",
        evaluations="custom.evaluations:runner",
        tasks="custom.tasks:runner",
        jobs="custom.jobs:jobs",
        tools="custom.tools:tools",
        allow_task_runs=True,
        allow_evaluation_runs=True,
        snapshot="api/openapi.json",
        tool_snapshot="api/tools.json",
        evaluation_snapshot="api/evaluations.json",
        title="Custom",
        version="2.0.0",
        description="Custom API",
        security_json='{"bearerAuth":{}}',
    )
    captured: McpServerOptions | None = None
    received_transport: str | None = None

    class _Server:
        def run(self, *, transport: str) -> None:
            nonlocal received_transport
            received_transport = transport

    def build(received: McpServerOptions) -> _Server:
        nonlocal captured
        captured = received
        return _Server()

    changed_to: Path | None = None

    def change_directory(path: Path) -> None:
        nonlocal changed_to
        changed_to = path

    monkeypatch.setattr(_mcp_server, "build_mcp_server", build)
    monkeypatch.setattr(os, "chdir", change_directory)

    _mcp_server.run_mcp_server(options)

    assert captured == options
    assert changed_to == tmp_path.resolve()
    assert received_transport == "stdio"


async def test_mcp_lists_the_stable_tool_surface_and_annotations() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()

    assert [tool.name for tool in result.tools] == [
        "app_map",
        "routes",
        "tools",
        "doctor",
        "preflight",
        "evaluation_list",
        "task_list",
        "openapi_diff",
        "tools_diff",
        "evaluation_diff",
        "make_preview",
        "verify",
        "check",
    ]
    for tool in result.tools:
        assert tool.outputSchema is not None
        assert tool.annotations is not None
        if tool.name in {"check", "verify"}:
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.destructiveHint is True
            assert tool.annotations.idempotentHint is False
            assert tool.annotations.openWorldHint is True
        elif tool.name == "preflight":
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
            assert tool.annotations.idempotentHint is True
            assert tool.annotations.openWorldHint is True
        else:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.openWorldHint is False

    enabled = build_mcp_server(
        McpServerOptions(
            EXAMPLE_ROOT,
            allow_task_runs=True,
            allow_evaluation_runs=True,
        )
    )
    enabled_tools = await enabled.list_tools()
    task_run = next(tool for tool in enabled_tools if tool.name == "task_run")
    assert task_run.annotations is not None
    assert task_run.annotations.readOnlyHint is False
    assert task_run.annotations.destructiveHint is True
    assert task_run.annotations.idempotentHint is False
    assert task_run.annotations.openWorldHint is True
    evaluation_run = next(
        tool for tool in enabled_tools if tool.name == "evaluation_run"
    )
    assert evaluation_run.annotations is not None
    assert evaluation_run.annotations.readOnlyHint is False
    assert evaluation_run.annotations.destructiveHint is True
    assert evaluation_run.annotations.idempotentHint is False
    assert evaluation_run.annotations.openWorldHint is True


async def test_mcp_inspection_and_preview_tools_return_versioned_results() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        routes = await session.call_tool("routes", {})
        tools = await session.call_tool("tools", {})
        app_map = await session.call_tool(
            "app_map", {"feature": "todos", "kinds": ["contract", "route"]}
        )
        doctor = await session.call_tool("doctor", {})
        preflight = await session.call_tool("preflight", {})
        evaluations = await session.call_tool("evaluation_list", {})
        tasks = await session.call_tool("task_list", {})
        preview = await session.call_tool(
            "make_preview", {"artifact": "feature", "name": "notes"}
        )
        conflict = await session.call_tool(
            "make_preview", {"artifact": "feature", "name": "todos"}
        )
        diff = await session.call_tool("openapi_diff", {})
        tool_diff = await session.call_tool("tools_diff", {})
        evaluation_diff = await session.call_tool("evaluation_diff", {})

    assert routes.isError is False
    assert routes.structuredContent is not None
    assert routes.structuredContent["schema_version"] == 6
    assert routes.structuredContent["root"] == str(EXAMPLE_ROOT)
    assert any(item["path"] == "/todos" for item in routes.structuredContent["routes"])

    assert tools.isError is False
    assert tools.structuredContent is not None
    assert tools.structuredContent["schema_version"] == 6
    assert tools.structuredContent["manifest"]["schema_version"] == 1
    assert tools.structuredContent["manifest"]["tools"] == []

    assert app_map.isError is False
    assert app_map.structuredContent is not None
    assert {node["kind"] for node in app_map.structuredContent["nodes"]} <= {
        "contract",
        "route",
    }
    assert app_map.structuredContent["summary"]["features"] == 0

    assert doctor.isError is False
    assert doctor.structuredContent is not None
    assert doctor.structuredContent["schema_version"] == 6

    assert preflight.isError is False
    assert preflight.structuredContent is not None
    assert preflight.structuredContent["schema_version"] == 6
    assert preflight.structuredContent["ok"] is True
    assert preflight.structuredContent["checks"] == []

    assert evaluations.isError is False
    assert evaluations.structuredContent is not None
    assert evaluations.structuredContent["schema_version"] == 6
    assert evaluations.structuredContent["evaluations"] == []

    assert tasks.isError is False
    assert tasks.structuredContent is not None
    assert tasks.structuredContent["schema_version"] == 6
    assert tasks.structuredContent["tasks"] == []

    assert preview.isError is False
    assert preview.structuredContent is not None
    assert preview.structuredContent["ok"] is True
    assert preview.structuredContent["dry_run"] is True
    assert not (EXAMPLE_ROOT / "app/features/notes").exists()

    assert conflict.isError is False
    assert conflict.structuredContent is not None
    assert conflict.structuredContent["ok"] is False

    assert diff.isError is False
    assert diff.structuredContent is not None
    assert diff.structuredContent["schema_version"] == 6
    assert diff.structuredContent["compatible"] is True

    assert tool_diff.isError is False
    assert tool_diff.structuredContent is not None
    assert tool_diff.structuredContent["schema_version"] == 6
    assert tool_diff.structuredContent["compatible"] is True

    assert evaluation_diff.isError is False
    assert evaluation_diff.structuredContent is not None
    assert evaluation_diff.structuredContent["schema_version"] == 6
    assert evaluation_diff.structuredContent["compatible"] is True


async def test_mcp_preflight_discards_application_output(
    tmp_path: Path,
) -> None:
    server_package = tmp_path / "app/server"
    server_package.mkdir(parents=True)
    (tmp_path / "app/__init__.py").write_text("")
    (server_package / "__init__.py").write_text("")
    (server_package / "preflight.py").write_text(
        "from tenchi.preflight import preflight_check, preflight_group\n"
        "print('preflight-import-secret')\n"
        "async def dependency() -> None:\n"
        "    print('preflight-check-secret')\n"
        "    raise RuntimeError('preflight-exception-secret')\n"
        "checks = preflight_group(preflight_check(\n"
        "    'dependency', dependency, failure_code='DEPENDENCY_UNAVAILABLE'\n"
        "))\n"
    )
    error_path = tmp_path / "mcp.stderr"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tenchi.cli", "mcp", "--root", str(tmp_path)],
        cwd=tmp_path,
    )

    with error_path.open("w+") as errors:
        async with (
            stdio_client(parameters, errlog=errors) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool("preflight", {})
        errors.seek(0)
        error_output = errors.read()

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["checks"][0]["failure_code"] == (
        "DEPENDENCY_UNAVAILABLE"
    )
    assert "preflight-import-secret" not in error_output
    assert "preflight-check-secret" not in error_output
    assert "preflight-exception-secret" not in error_output


async def test_mcp_task_execution_is_opt_in_and_returns_structured_results(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "operations.py").write_text(
        "from pydantic import BaseModel\n"
        "from tenchi.tasks import create_task_runner, task, task_group\n"
        "class Input(BaseModel):\n"
        "    value: int\n"
        "class Result(BaseModel):\n"
        "    doubled: int\n"
        "async def double(request: Input, context: object) -> Result:\n"
        "    print('task chatter')\n"
        "    return Result(doubled=request.value * 2)\n"
        "async def optional(\n"
        "    request: str | None = 'default', context: object = None\n"
        ") -> str:\n"
        "    return 'null' if request is None else request\n"
        "runner = create_task_runner(\n"
        "    tasks=task_group(\n"
        "        task('numbers.double', double),\n"
        "        task('numbers.optional', optional),\n"
        "    ),\n"
        "    context_factory=lambda: object(),\n"
        ")\n"
    )
    disabled = build_mcp_server(McpServerOptions(tmp_path, tasks="operations:runner"))
    assert "task_run" not in {tool.name for tool in await disabled.list_tools()}

    enabled = build_mcp_server(
        McpServerOptions(
            tmp_path,
            tasks="operations:runner",
            allow_task_runs=True,
        )
    )
    async with create_connected_server_and_client_session(enabled) as session:
        listed = await session.call_tool("task_list", {})
        ran = await session.call_tool(
            "task_run",
            {"name": "numbers.double", "input": {"value": 4}},
        )
        invalid = await session.call_tool(
            "task_run",
            {"name": "numbers.double", "input": {"value": "bad"}},
        )
        omitted = await session.call_tool(
            "task_run",
            {"name": "numbers.optional"},
        )
        explicit_null = await session.call_tool(
            "task_run",
            {"name": "numbers.optional", "input": None},
        )

    assert listed.isError is False
    assert listed.structuredContent is not None
    assert listed.structuredContent["tasks"][0]["name"] == "numbers.double"
    assert ran.isError is False
    assert ran.structuredContent is not None
    assert ran.structuredContent["ok"] is True
    assert ran.structuredContent["output"] == {"doubled": 8}
    assert invalid.isError is False
    assert invalid.structuredContent is not None
    assert invalid.structuredContent["ok"] is False
    assert invalid.structuredContent["error"]["kind"] == "invalid_input"
    assert omitted.structuredContent is not None
    assert omitted.structuredContent["output"] == "default"
    assert explicit_null.structuredContent is not None
    assert explicit_null.structuredContent["output"] == "null"


async def test_mcp_evaluation_execution_is_opt_in_and_redacted(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "application_evaluations.py").write_text(
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
        "    return evaluation_result(\n"
        "        scores={'quality': 1.0}, tokens=7, cost_usd=0.001\n"
        "    )\n"
        "suite = evaluation(\n"
        "    'answers.quality',\n"
        "    case=Case,\n"
        "    cases=(evaluation_case(\n"
        "        'answers.quality.simple', Case(prompt='private prompt')\n"
        "    ),),\n"
        "    metrics=(evaluation_metric('quality', threshold=0.9),),\n"
        "    evaluator=score,\n"
        "    kind='model',\n"
        "    max_tokens=10,\n"
        "    max_cost_usd=0.01,\n"
        ")\n"
        "runner = create_evaluation_runner(\n"
        "    evaluations=evaluation_group(suite),\n"
        "    context_factory=lambda: object(),\n"
        ")\n",
        encoding="utf-8",
    )
    options = McpServerOptions(
        tmp_path,
        evaluations="application_evaluations:runner",
    )
    disabled = build_mcp_server(options)
    assert "evaluation_run" not in {tool.name for tool in await disabled.list_tools()}

    enabled = build_mcp_server(
        McpServerOptions(
            tmp_path,
            evaluations="application_evaluations:runner",
            allow_evaluation_runs=True,
        )
    )
    async with create_connected_server_and_client_session(enabled) as session:
        listed = await session.call_tool("evaluation_list", {})
        ran = await session.call_tool(
            "evaluation_run",
            {"name": "answers.quality"},
        )
        unknown = await session.call_tool(
            "evaluation_run",
            {"name": "answers.missing"},
        )

    assert listed.isError is False
    assert listed.structuredContent is not None
    assert listed.structuredContent["evaluations"][0]["cases"] == [
        "answers.quality.simple"
    ]
    assert "private prompt" not in str(listed.structuredContent)

    assert ran.isError is False
    assert ran.structuredContent is not None
    assert ran.structuredContent["ok"] is True
    assert ran.structuredContent["counts"]["completed"] == 1
    assert "private prompt" not in str(ran.structuredContent)

    assert unknown.isError is False
    assert unknown.structuredContent is not None
    assert unknown.structuredContent["error"]["code"] == "EVALUATION_NOT_FOUND"


async def test_mcp_returns_tool_errors_for_invalid_boundaries() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        unknown = await session.call_tool("app_map", {"feature": "missing"})
        escaped = await session.call_tool(
            "openapi_diff", {"snapshot": "../openapi.json"}
        )
        empty = await session.call_tool("openapi_diff", {"snapshot": ""})
        escaped_tools = await session.call_tool(
            "tools_diff", {"snapshot": "../tools.json"}
        )
        empty_tools = await session.call_tool("tools_diff", {"snapshot": ""})
        escaped_evaluations = await session.call_tool(
            "evaluation_diff", {"snapshot": "../evaluations.json"}
        )
        empty_evaluations = await session.call_tool("evaluation_diff", {"snapshot": ""})
        misplaced_evaluation_override = await session.call_tool(
            "evaluation_diff", {"allow_missing_baseline": True}
        )
        invalid_preview = await session.call_tool(
            "make_preview",
            {"artifact": "use-case", "name": "create_note"},
        )

    assert unknown.isError is True
    assert escaped.isError is True
    assert empty.isError is True
    assert escaped_tools.isError is True
    assert empty_tools.isError is True
    assert escaped_evaluations.isError is True
    assert empty_evaluations.isError is True
    assert misplaced_evaluation_override.isError is True
    assert invalid_preview.isError is True


async def test_mcp_exposes_project_agent_instructions(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "AGENTS.md").write_text("# Local agent rules\n")
    server = build_mcp_server(McpServerOptions(tmp_path))

    async with create_connected_server_and_client_session(server) as session:
        local = await session.read_resource(AnyUrl("tenchi://project/agents"))

    local_content = cast(TextResourceContents, local.contents[0])
    assert local_content.mimeType == "text/markdown"
    assert local_content.text == "# Local agent rules\n"

    (tmp_path / "AGENTS.md").unlink()
    fallback_server = build_mcp_server(McpServerOptions(tmp_path))
    async with create_connected_server_and_client_session(fallback_server) as session:
        fallback = await session.read_resource(AnyUrl("tenchi://project/agents"))

    fallback_content = cast(TextResourceContents, fallback.contents[0])
    assert "Run `app_map`" in fallback_content.text


async def test_mcp_rejects_agent_instructions_outside_the_root(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "app").mkdir(parents=True)
    outside = tmp_path / "outside-agents.md"
    outside.write_text("# Outside rules\n")
    (project / "AGENTS.md").symlink_to(outside)
    server = build_mcp_server(McpServerOptions(project))

    with pytest.raises(ResourceError, match="must stay inside"):
        await server.read_resource(AnyUrl("tenchi://project/agents"))


@pytest.mark.parametrize("tool_name", ["check", "verify"])
@pytest.mark.parametrize("snapshot_name", ["openapi.json", "tools.json"])
async def test_mcp_revalidates_validation_snapshots_for_each_call(
    tmp_path: Path,
    tool_name: str,
    snapshot_name: str,
) -> None:
    (tmp_path / "app").mkdir()
    server = build_mcp_server(McpServerOptions(tmp_path))
    outside = tmp_path.parent / f"{tmp_path.name}-outside-{snapshot_name}"
    outside.write_text("{}")
    (tmp_path / snapshot_name).symlink_to(outside)

    async with create_connected_server_and_client_session(server) as session:
        arguments = {"base_ref": "HEAD"} if tool_name == "verify" else {}
        result = await session.call_tool(tool_name, arguments)

    assert result.isError is True
    assert result.content
    assert isinstance(result.content[0], TextContent)
    assert "must stay inside" in result.content[0].text


async def test_mcp_check_returns_failed_validation_as_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run_check(root: Path, **kwargs: object) -> CheckResult:
        captured.update(kwargs)
        return CheckResult(
            root=str(root.resolve()),
            ok=False,
            steps=(),
            duration_seconds=0.0,
            error="validation could not start",
        )

    monkeypatch.setattr(_mcp_server, "run_check", fake_run_check)
    server = build_mcp_server(
        McpServerOptions(
            EXAMPLE_ROOT,
            title="Custom API",
            version="2.0.0",
            description="Custom description",
            security_json='{"apiKey":{"type":"apiKey","in":"header","name":"x-key"}}',
        )
    )

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("check", {"timeout_seconds": 10})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["error"] == "validation could not start"
    assert captured["title"] == "Custom API"
    assert captured["version"] == "2.0.0"
    assert captured["description"] == "Custom description"
    assert captured["snapshot"] == str((EXAMPLE_ROOT / "openapi.json").resolve())
    assert captured["tool_snapshot"] == str((EXAMPLE_ROOT / "tools.json").resolve())
    assert captured["security_json"] == (
        '{"apiKey":{"type":"apiKey","in":"header","name":"x-key"}}'
    )


async def test_mcp_verify_returns_the_shared_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_verification_result(root: Path, **kwargs: object) -> VerificationResult:
        captured["root"] = root
        captured.update(kwargs)
        return VerificationResult(
            root=str(root.resolve()),
            baseline_ref=str(kwargs["base_ref"]),
            baseline_commit=None,
            duration_seconds=0.01,
            check=None,
            architecture=None,
            openapi=None,
            tools=None,
            evaluations=None,
            errors=(
                VerificationErrorResult(
                    stage="baseline",
                    message="unknown ref",
                ),
            ),
        )

    monkeypatch.setattr(
        _mcp_server,
        "verification_result",
        fake_verification_result,
    )
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "verify",
            {
                "base_ref": "origin/main",
                "timeout_seconds": 10,
                "allow_missing_evaluation_baseline": True,
            },
        )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["schema_version"] == 6
    assert result.structuredContent["tenchi_version"] == __version__
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["baseline"] == {
        "ref": "origin/main",
        "commit": None,
    }
    assert result.structuredContent["errors"] == [
        {"stage": "baseline", "message": "unknown ref"}
    ]
    assert captured["root"] == EXAMPLE_ROOT
    assert captured["routes"] == "app.server.routes:api_routes"
    assert captured["tasks"] == "app.server.tasks:runner"
    assert captured["jobs"] == "app.server.jobs:jobs"
    assert captured["tools"] == "app.server.tools:tools"
    assert captured["timeout_seconds"] == 10
    assert captured["allow_missing_evaluation_baseline"] is True


async def test_mcp_verify_rejects_null_bytes_as_a_structured_failure() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "verify",
            {"base_ref": "main\u0000other", "timeout_seconds": 10},
        )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["baseline"] == {
        "ref": "main\u0000other",
        "commit": None,
    }
    assert result.structuredContent["errors"] == [
        {
            "stage": "baseline",
            "message": (
                "Git ref must be non-empty, must not start with '-', and must "
                "contain neither whitespace nor control characters"
            ),
        }
    ]


async def test_mcp_cancellation_reaches_the_check_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    stopped = Event()

    def fake_run_check(root: Path, **kwargs: object) -> CheckResult:
        del root
        cancelled = cast(Callable[[], bool], kwargs["cancelled"])
        started.set()
        while not cancelled():
            sleep(0.01)
        stopped.set()
        raise CheckCancelled

    monkeypatch.setattr(_mcp_server, "run_check", fake_run_check)
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    call = asyncio.create_task(server.call_tool("check", {"timeout_seconds": 10}))
    assert await asyncio.to_thread(started.wait, 1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert await asyncio.to_thread(stopped.wait, 1)


async def test_mcp_cancellation_reaches_the_verification_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    stopped = Event()

    def fake_verification_result(root: Path, **kwargs: object) -> VerificationResult:
        del root
        cancelled = cast(Callable[[], bool], kwargs["cancelled"])
        started.set()
        while not cancelled():
            sleep(0.01)
        stopped.set()
        raise CheckCancelled

    monkeypatch.setattr(
        _mcp_server,
        "verification_result",
        fake_verification_result,
    )
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    call = asyncio.create_task(
        server.call_tool("verify", {"base_ref": "HEAD", "timeout_seconds": 10})
    )
    assert await asyncio.to_thread(started.wait, 1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert await asyncio.to_thread(stopped.wait, 1)


async def test_mcp_cli_serves_tools_over_stdio() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "tenchi.cli",
            "mcp",
            "--root",
            str(EXAMPLE_ROOT),
            "--title",
            "Renamed API",
        ],
        cwd=EXAMPLE_ROOT,
    )

    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        listed = await session.list_tools()
        routes = await session.call_tool("routes", {})
        diff = await session.call_tool("openapi_diff", {})

    assert {tool.name for tool in listed.tools} == {
        "app_map",
        "routes",
        "tools",
        "doctor",
        "preflight",
        "evaluation_list",
        "task_list",
        "openapi_diff",
        "tools_diff",
        "evaluation_diff",
        "make_preview",
        "verify",
        "check",
    }
    assert initialized.serverInfo.name == "Tenchi"
    assert initialized.serverInfo.version == __version__
    assert routes.isError is False
    assert routes.structuredContent is not None
    assert routes.structuredContent["schema_version"] == 6
    assert diff.isError is False
    assert diff.structuredContent is not None
    assert diff.structuredContent["counts"]["metadata"] == 1


async def test_mcp_keeps_application_import_output_off_protocol_stdout(
    tmp_path: Path,
) -> None:
    package = tmp_path / "app/server"
    package.mkdir(parents=True)
    (tmp_path / "app/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "routes.py").write_text(
        """print("application import output")

from tenchi.contracts import contract
from tenchi.routes import route, route_group

class AppContext:
    pass

async def ping(context: AppContext) -> str:
    del context
    return "pong"

ping_contract = contract(method="GET", path="/ping", response=str)
api_routes = route_group(route(ping_contract, ping))
routes = api_routes
"""
    )
    error_path = tmp_path / "mcp.stderr"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tenchi.cli", "mcp", "--root", str(tmp_path)],
        cwd=tmp_path,
    )

    with error_path.open("w+") as errors:
        async with (
            stdio_client(parameters, errlog=errors) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            result = await session.call_tool("routes", {})
        errors.seek(0)
        error_output = errors.read()

    assert result.isError is False
    assert "application import output" in error_output


async def test_mcp_route_inspection_reloads_application_edits(tmp_path: Path) -> None:
    package = tmp_path / "app/server"
    package.mkdir(parents=True)
    (tmp_path / "app/__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    routes_path = package / "routes.py"

    def write_routes(path: str) -> None:
        routes_path.write_text(
            f'''from tenchi.contracts import contract
from tenchi.routes import route, route_group

class AppContext:
    pass

async def ping(context: AppContext) -> str:
    del context
    return "pong"

ping_contract = contract(method="GET", path="{path}", response=str)
api_routes = route_group(route(ping_contract, ping))
routes = api_routes
'''
        )

    write_routes("/first")
    server = build_mcp_server(McpServerOptions(tmp_path))
    async with create_connected_server_and_client_session(server) as session:
        first = await session.call_tool("routes", {})
        timestamp = routes_path.stat().st_mtime_ns
        write_routes("/later")
        os.utime(routes_path, ns=(timestamp, timestamp))
        updated = await session.call_tool("routes", {})

    assert first.structuredContent is not None
    assert first.structuredContent["routes"][0]["path"] == "/first"
    assert updated.structuredContent is not None
    assert updated.structuredContent["routes"][0]["path"] == "/later"
