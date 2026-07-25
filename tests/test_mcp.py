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


async def test_mcp_lists_the_stable_tool_surface_and_annotations() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.list_tools()

    assert [tool.name for tool in result.tools] == [
        "app_map",
        "routes",
        "doctor",
        "preflight",
        "task_list",
        "openapi_diff",
        "make_preview",
        "check",
    ]
    for tool in result.tools:
        assert tool.outputSchema is not None
        assert tool.annotations is not None
        if tool.name == "check":
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

    enabled = build_mcp_server(McpServerOptions(EXAMPLE_ROOT, allow_task_runs=True))
    enabled_tools = await enabled.list_tools()
    task_run = next(tool for tool in enabled_tools if tool.name == "task_run")
    assert task_run.annotations is not None
    assert task_run.annotations.readOnlyHint is False
    assert task_run.annotations.destructiveHint is True
    assert task_run.annotations.idempotentHint is False
    assert task_run.annotations.openWorldHint is True


async def test_mcp_inspection_and_preview_tools_return_versioned_results() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        routes = await session.call_tool("routes", {})
        app_map = await session.call_tool(
            "app_map", {"feature": "todos", "kinds": ["contract", "route"]}
        )
        doctor = await session.call_tool("doctor", {})
        preflight = await session.call_tool("preflight", {})
        tasks = await session.call_tool("task_list", {})
        preview = await session.call_tool(
            "make_preview", {"artifact": "feature", "name": "notes"}
        )
        conflict = await session.call_tool(
            "make_preview", {"artifact": "feature", "name": "todos"}
        )
        diff = await session.call_tool("openapi_diff", {})

    assert routes.isError is False
    assert routes.structuredContent is not None
    assert routes.structuredContent["schema_version"] == 2
    assert routes.structuredContent["root"] == str(EXAMPLE_ROOT)
    assert any(item["path"] == "/todos" for item in routes.structuredContent["routes"])

    assert app_map.isError is False
    assert app_map.structuredContent is not None
    assert {node["kind"] for node in app_map.structuredContent["nodes"]} <= {
        "contract",
        "route",
    }
    assert app_map.structuredContent["summary"]["features"] == 0

    assert doctor.isError is False
    assert doctor.structuredContent is not None
    assert doctor.structuredContent["schema_version"] == 2

    assert preflight.isError is False
    assert preflight.structuredContent is not None
    assert preflight.structuredContent["schema_version"] == 2
    assert preflight.structuredContent["ok"] is True
    assert preflight.structuredContent["checks"] == []

    assert tasks.isError is False
    assert tasks.structuredContent is not None
    assert tasks.structuredContent["schema_version"] == 2
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
    assert diff.structuredContent["schema_version"] == 2
    assert diff.structuredContent["compatible"] is True


async def test_mcp_preflight_discards_application_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
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
    server = build_mcp_server(McpServerOptions(tmp_path))

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("preflight", {})

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is False
    assert result.structuredContent["checks"][0]["failure_code"] == (
        "DEPENDENCY_UNAVAILABLE"
    )
    captured = capsys.readouterr()
    assert "preflight-import-secret" not in captured.out + captured.err
    assert "preflight-check-secret" not in captured.out + captured.err
    assert "preflight-exception-secret" not in captured.out + captured.err


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


async def test_mcp_returns_tool_errors_for_invalid_boundaries() -> None:
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT))

    async with create_connected_server_and_client_session(server) as session:
        unknown = await session.call_tool("app_map", {"feature": "missing"})
        escaped = await session.call_tool(
            "openapi_diff", {"snapshot": "../openapi.json"}
        )
        empty = await session.call_tool("openapi_diff", {"snapshot": ""})
        invalid_preview = await session.call_tool(
            "make_preview",
            {"artifact": "use-case", "name": "create_note"},
        )

    assert unknown.isError is True
    assert escaped.isError is True
    assert empty.isError is True
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


async def test_mcp_revalidates_the_check_snapshot_for_each_call(
    tmp_path: Path,
) -> None:
    (tmp_path / "app").mkdir()
    server = build_mcp_server(McpServerOptions(tmp_path))
    outside = tmp_path.parent / f"{tmp_path.name}-outside-openapi.json"
    outside.write_text("{}")
    (tmp_path / "openapi.json").symlink_to(outside)

    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("check", {})

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
    assert captured["security_json"] == (
        '{"apiKey":{"type":"apiKey","in":"header","name":"x-key"}}'
    )


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
        "doctor",
        "preflight",
        "task_list",
        "openapi_diff",
        "make_preview",
        "check",
    }
    assert initialized.serverInfo.name == "Tenchi"
    assert initialized.serverInfo.version == __version__
    assert routes.isError is False
    assert routes.structuredContent is not None
    assert routes.structuredContent["schema_version"] == 2
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
