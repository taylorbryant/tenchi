"""Guard the versioned JSON contracts consumed by agents and automation.

The snapshot records every structured CLI result plus every MCP tool input and
output schema. Additive changes may update the current version's snapshot.
Breaking or unknown changes require bumping ``AGENT_PROTOCOL_VERSION`` and
creating a new versioned snapshot, leaving older snapshots intact.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from tenchi._agent_protocol import (
    AGENT_RESULT_NAMES,
    AgentResultName,
    agent_result_schemas,
    validate_agent_result,
)
from tenchi._cli_results import (
    AGENT_PROTOCOL_VERSION,
    MakeResult,
)
from tenchi._mcp_server import McpServerOptions, build_mcp_server
from tenchi._schema_compatibility import (
    ChangeSeverity,
    Direction,
    compare_schema,
)

REPOSITORY_ROOT = Path(__file__).parent.parent
EXAMPLE_ROOT = REPOSITORY_ROOT / "examples" / "todos"
SNAPSHOT_DIRECTORY = Path(__file__).parent
SNAPSHOT_PATH = (
    SNAPSHOT_DIRECTORY / f"agent_protocol_v{AGENT_PROTOCOL_VERSION}_snapshot.json"
)
SNAPSHOT_NAME = re.compile(r"agent_protocol_v([1-9][0-9]*)_snapshot\.json")
UPDATE_ENVIRONMENT = "TENCHI_UPDATE_AGENT_PROTOCOL_SNAPSHOT"
BASE_REF_ENVIRONMENT = "TENCHI_AGENT_PROTOCOL_BASE_REF"
MCP_RESULT_NAMES: dict[str, AgentResultName] = {
    "app_map": "app_map",
    "routes": "routes",
    "doctor": "doctor",
    "openapi_diff": "openapi_diff",
    "make_preview": "make",
    "check": "check",
    "task_list": "task_list",
    "task_run": "task_run",
}

type JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class _ProtocolChange:
    severity: ChangeSeverity
    location: str
    message: str


class _ChangeSink:
    def __init__(self) -> None:
        self.changes: list[_ProtocolChange] = []

    def add(self, severity: ChangeSeverity, location: str, message: str) -> None:
        self.changes.append(_ProtocolChange(severity, location, message))


async def _render_agent_protocol() -> JsonObject:
    cli_results = agent_result_schemas()
    server = build_mcp_server(McpServerOptions(EXAMPLE_ROOT, allow_task_runs=True))
    mcp_tools: dict[str, JsonObject] = {}
    for tool in await server.list_tools():
        assert tool.outputSchema is not None, (
            f"MCP tool {tool.name!r} must declare structured output"
        )
        mcp_tools[tool.name] = {
            "input": cast(JsonObject, tool.inputSchema),
            "output": cast(JsonObject, tool.outputSchema),
        }
    return {
        "schema_version": AGENT_PROTOCOL_VERSION,
        "cli_results": cli_results,
        "mcp_tools": mcp_tools,
    }


async def test_agent_protocol_matches_its_versioned_snapshot() -> None:
    current = await _render_agent_protocol()
    _assert_embedded_versions(current, expected_version=AGENT_PROTOCOL_VERSION)

    if os.environ.get(UPDATE_ENVIRONMENT):
        if SNAPSHOT_PATH.exists():
            baseline = _load_snapshot()
            incompatible = _incompatible_changes(baseline, current)
            assert not incompatible, _version_bump_message(incompatible)
        SNAPSHOT_PATH.write_text(_render_json(current), encoding="utf-8")

    assert SNAPSHOT_PATH.exists(), (
        f"No agent protocol v{AGENT_PROTOCOL_VERSION} snapshot found. Generate "
        f"one with {UPDATE_ENVIRONMENT}=1 uv run pytest "
        "tests/test_agent_protocol.py"
    )
    baseline = _load_snapshot()
    incompatible = _incompatible_changes(baseline, current)
    assert not incompatible, _version_bump_message(incompatible)
    assert current == baseline, (
        "The agent protocol changed without updating its canonical snapshot. "
        f"If the change is additive, regenerate with {UPDATE_ENVIRONMENT}=1 "
        "uv run pytest tests/test_agent_protocol.py and review the diff. "
        "Breaking or unknown changes require a new protocol version.\n\n"
        f"{_diff(baseline, current)}"
    )


def test_agent_protocol_snapshot_history_is_complete() -> None:
    snapshots = _versioned_snapshot_paths()
    expected_versions = set(range(1, AGENT_PROTOCOL_VERSION + 1))

    assert set(snapshots) == expected_versions, (
        "Agent protocol snapshots must be contiguous and retained: expected "
        f"{sorted(expected_versions)}, found {sorted(snapshots)}"
    )
    for version, path in snapshots.items():
        protocol = _load_snapshot(path)
        _assert_embedded_versions(protocol, expected_version=version)


async def test_agent_protocol_matches_its_git_baseline() -> None:
    base_ref = os.environ.get(BASE_REF_ENVIRONMENT)
    if base_ref is None:
        return

    current = await _render_agent_protocol()
    _assert_git_history(
        base_ref=base_ref,
        baseline_snapshots=_git_snapshot_texts(base_ref),
        local_snapshots=_versioned_snapshot_paths(),
        current=current,
        current_version=AGENT_PROTOCOL_VERSION,
    )


def test_cli_agent_renderers_use_registered_result_contracts() -> None:
    schemas = agent_result_schemas()
    assert set(schemas) == set(AGENT_RESULT_NAMES)

    tree = ast.parse((REPOSITORY_ROOT / "src/tenchi/cli.py").read_text())
    renderer = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_print_agent_json"
    )
    rendered_names: set[str] = set()
    json_dump_lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "json"
            and node.func.attr == "dumps"
        ):
            json_dump_lines.append(node.lineno)
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_print_agent_json"
        ):
            continue
        assert node.args, "_print_agent_json calls must identify their result contract"
        result_name = node.args[0]
        assert isinstance(result_name, ast.Constant) and isinstance(
            result_name.value, str
        ), "_print_agent_json result names must be string literals"
        rendered_names.add(result_name.value)

    assert rendered_names == set(AGENT_RESULT_NAMES)
    assert renderer.end_lineno is not None
    assert len(json_dump_lines) == 1
    assert renderer.lineno <= json_dump_lines[0] <= renderer.end_lineno


def test_agent_result_registry_rejects_mismatched_payloads() -> None:
    make_payload = MakeResult(
        root="/tmp/app",
        artifact="feature",
        name="notes",
        feature=None,
        dry_run=True,
        ok=True,
        files=("app/features/notes/routes.py",),
        next_steps=(),
    ).as_dict()

    with pytest.raises(ValueError, match="Agent result 'doctor'"):
        validate_agent_result("doctor", make_payload)


def test_agent_result_registry_rejects_extra_fields() -> None:
    doctor_payload: dict[str, object] = {
        "schema_version": AGENT_PROTOCOL_VERSION,
        "root": "/tmp/app",
        "ok": True,
        "diagnostics": [],
        "unexpected": True,
    }

    with pytest.raises(ValueError, match="does not exactly match"):
        validate_agent_result("doctor", doctor_payload)


async def test_mcp_outputs_use_the_cli_result_contracts() -> None:
    protocol = await _render_agent_protocol()
    cli_results = _object(protocol["cli_results"])
    mcp_tools = _object(protocol["mcp_tools"])

    assert set(mcp_tools) == set(MCP_RESULT_NAMES)
    for tool_name, result_name in MCP_RESULT_NAMES.items():
        tool = _object(mcp_tools[tool_name])
        assert tool["output"] == cli_results[result_name]


def test_same_version_guard_rejects_breaking_result_changes() -> None:
    baseline = _load_snapshot()
    current = deepcopy(baseline)
    cli_results = _object(current["cli_results"])
    doctor = _object(cli_results["doctor"])
    properties = _object(doctor["properties"])
    properties.pop("ok")

    changes = _incompatible_changes(baseline, current)

    assert any(
        change.severity == "breaking"
        and change.location == "CLI result 'doctor' property 'ok'"
        for change in changes
    )


def test_same_version_guard_rejects_required_tool_inputs() -> None:
    baseline = _load_snapshot()
    current = deepcopy(baseline)
    tools = _object(current["mcp_tools"])
    check_input = _object(_object(tools["check"])["input"])
    properties = _object(check_input["properties"])
    properties["mode"] = {"type": "string"}
    check_input["required"] = ["mode"]

    changes = _incompatible_changes(baseline, current)

    assert any(
        change.severity == "breaking"
        and change.location == "MCP tool 'check' input property 'mode'"
        for change in changes
    )


def test_same_version_guard_allows_optional_tool_inputs() -> None:
    baseline = _load_snapshot()
    current = deepcopy(baseline)
    tools = _object(current["mcp_tools"])
    check_input = _object(_object(tools["check"])["input"])
    properties = _object(check_input["properties"])
    properties["verbosity"] = {"type": "integer"}

    assert _incompatible_changes(baseline, current) == []


def test_git_history_rejects_same_version_breaking_changes() -> None:
    baseline = _load_snapshot()
    historical = SNAPSHOT_DIRECTORY / "agent_protocol_v1_snapshot.json"
    current = deepcopy(baseline)
    cli_results = _object(current["cli_results"])
    doctor = _object(cli_results["doctor"])
    _object(doctor["properties"]).pop("ok")

    with pytest.raises(AssertionError, match="changed incompatibly"):
        _assert_git_history(
            base_ref="base",
            baseline_snapshots={
                1: historical.read_text(encoding="utf-8"),
                2: _render_json(baseline),
            },
            local_snapshots={1: historical, 2: SNAPSHOT_PATH},
            current=current,
            current_version=2,
        )


def test_git_history_rejects_changed_historical_snapshots(tmp_path: Path) -> None:
    baseline = _load_snapshot()
    historical = SNAPSHOT_DIRECTORY / "agent_protocol_v1_snapshot.json"
    changed = tmp_path / historical.name
    changed.write_text(_render_json({"schema_version": 1}), encoding="utf-8")

    with pytest.raises(AssertionError, match="historical"):
        _assert_git_history(
            base_ref="base",
            baseline_snapshots={
                1: historical.read_text(encoding="utf-8"),
                2: _render_json(baseline),
            },
            local_snapshots={1: changed, 2: SNAPSHOT_PATH},
            current=baseline,
            current_version=2,
        )


def _assert_embedded_versions(
    protocol: JsonObject,
    *,
    expected_version: int,
) -> None:
    version_value = protocol["schema_version"]
    assert (
        isinstance(version_value, int)
        and not isinstance(version_value, bool)
        and version_value == expected_version
    ), (
        f"Agent protocol snapshot declares version {version_value!r}; "
        f"expected {expected_version}"
    )
    cli_results = _object(protocol["cli_results"])
    mcp_tools = _object(protocol["mcp_tools"])
    for name, schema_value in cli_results.items():
        _assert_schema_version(
            _object(schema_value), expected_version, f"CLI result {name!r}"
        )
    for name, tool_value in mcp_tools.items():
        output = _object(_object(tool_value)["output"])
        _assert_schema_version(output, expected_version, f"MCP tool {name!r}")


def _assert_schema_version(
    schema: JsonObject,
    version: int,
    label: str,
) -> None:
    properties = _object(schema["properties"])
    version_schema = _resolve_local_reference(
        schema, _object(properties["schema_version"])
    )
    assert version_schema.get("const") == version, (
        f"{label} does not embed agent protocol version {version}"
    )


def _resolve_local_reference(schema: JsonObject, value: JsonObject) -> JsonObject:
    reference = value.get("$ref")
    if not isinstance(reference, str):
        return value
    assert reference.startswith("#/")
    resolved: object = schema
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        resolved = _object(resolved)[part]
    return _object(resolved)


def _incompatible_changes(
    baseline: JsonObject,
    current: JsonObject,
) -> list[_ProtocolChange]:
    changes: list[_ProtocolChange] = []
    changes.extend(
        _compare_schema_sections(
            _object(baseline["cli_results"]),
            _object(current["cli_results"]),
            direction="output",
            location="CLI result",
        )
    )

    baseline_tools = _object(baseline["mcp_tools"])
    current_tools = _object(current["mcp_tools"])
    for name in sorted(set(baseline_tools) - set(current_tools)):
        changes.append(_ProtocolChange("breaking", f"MCP tool {name!r}", "removed"))
    for name in sorted(set(baseline_tools) & set(current_tools)):
        before = _object(baseline_tools[name])
        after = _object(current_tools[name])
        schema_fields: tuple[tuple[str, Direction], ...] = (
            ("input", "input"),
            ("output", "output"),
        )
        for field, direction in schema_fields:
            changes.extend(
                _compare_schemas(
                    _object(before[field]),
                    _object(after[field]),
                    direction=direction,
                    location=f"MCP tool {name!r} {field}",
                )
            )
    return [change for change in changes if change.severity in ("breaking", "unknown")]


def _compare_schema_sections(
    baseline: JsonObject,
    current: JsonObject,
    *,
    direction: Direction,
    location: str,
) -> list[_ProtocolChange]:
    changes = [
        _ProtocolChange("breaking", f"{location} {name!r}", "removed")
        for name in sorted(set(baseline) - set(current))
    ]
    for name in sorted(set(baseline) & set(current)):
        changes.extend(
            _compare_schemas(
                _object(baseline[name]),
                _object(current[name]),
                direction=direction,
                location=f"{location} {name!r}",
            )
        )
    return changes


def _compare_schemas(
    baseline: JsonObject,
    current: JsonObject,
    *,
    direction: Direction,
    location: str,
) -> list[_ProtocolChange]:
    sink = _ChangeSink()
    compare_schema(
        _without_definitions(baseline),
        _without_definitions(current),
        direction=direction,
        baseline=baseline,
        current=current,
        location=location,
        changes=sink,
    )
    return sink.changes


def _without_definitions(schema: JsonObject) -> JsonObject:
    return {key: value for key, value in schema.items() if key != "$defs"}


def _versioned_snapshot_paths() -> dict[int, Path]:
    snapshots: dict[int, Path] = {}
    for path in SNAPSHOT_DIRECTORY.glob("agent_protocol_v*_snapshot.json"):
        match = SNAPSHOT_NAME.fullmatch(path.name)
        assert match is not None, f"Invalid agent protocol snapshot name: {path.name}"
        version = int(match.group(1))
        assert version not in snapshots
        snapshots[version] = path
    return snapshots


def _load_snapshot(path: Path = SNAPSHOT_PATH) -> JsonObject:
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    return _object(parsed)


def _git_snapshot_texts(base_ref: str) -> dict[int, str]:
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_ref, "--", "tests"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, (
        f"Could not inspect agent protocol snapshots at {base_ref!r}: "
        f"{listed.stderr.strip()}"
    )

    snapshots: dict[int, str] = {}
    for relative in listed.stdout.splitlines():
        path = Path(relative)
        match = SNAPSHOT_NAME.fullmatch(path.name)
        if path.parent != Path("tests") or match is None:
            continue
        version = int(match.group(1))
        shown = subprocess.run(
            ["git", "show", f"{base_ref}:{relative}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert shown.returncode == 0, (
            f"Could not read {relative} at {base_ref!r}: {shown.stderr.strip()}"
        )
        snapshots[version] = shown.stdout
    return snapshots


def _assert_git_history(
    *,
    base_ref: str,
    baseline_snapshots: dict[int, str],
    local_snapshots: dict[int, Path],
    current: JsonObject,
    current_version: int,
) -> None:
    if not baseline_snapshots:
        assert current_version == 1, (
            "A repository without an agent protocol baseline must start at version 1"
        )
        return

    previous_version = max(baseline_snapshots)
    assert set(baseline_snapshots) == set(range(1, previous_version + 1)), (
        f"Agent protocol history at {base_ref!r} is not contiguous"
    )
    assert current_version in (previous_version, previous_version + 1), (
        f"Agent protocol version must remain at {previous_version} or advance to "
        f"{previous_version + 1}; found {current_version}"
    )

    for version, baseline_text in baseline_snapshots.items():
        if version >= current_version:
            continue
        assert version in local_snapshots, (
            f"Agent protocol v{version} is historical and must be retained"
        )
        local_text = local_snapshots[version].read_text(encoding="utf-8")
        assert local_text == baseline_text, (
            f"Agent protocol v{version} is historical and must remain byte-for-byte "
            f"identical to {base_ref!r}"
        )

    if current_version != previous_version:
        return
    baseline = _object(json.loads(baseline_snapshots[current_version]))
    incompatible = _incompatible_changes(baseline, current)
    assert not incompatible, _version_bump_message(incompatible)


def _object(value: object) -> JsonObject:
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(JsonObject, mapping)


def _version_bump_message(changes: list[_ProtocolChange]) -> str:
    details = "\n".join(
        f"- [{change.severity}] {change.location}: {change.message}"
        for change in changes
    )
    return (
        f"Agent protocol v{AGENT_PROTOCOL_VERSION} changed incompatibly. "
        "Bump AGENT_PROTOCOL_VERSION, update every result's embedded version, "
        f"and generate a new snapshot instead of replacing {SNAPSHOT_PATH.name}."
        f"\n{details}"
    )


def _render_json(value: JsonObject) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _diff(baseline: JsonObject, current: JsonObject) -> str:
    lines = list(
        difflib.unified_diff(
            _render_json(baseline).splitlines(),
            _render_json(current).splitlines(),
            fromfile=SNAPSHOT_PATH.name,
            tofile="current agent protocol",
            lineterm="",
        )
    )
    preview = lines[:200]
    if len(lines) > len(preview):
        preview.append(f"... diff truncated ({len(lines) - len(preview)} more lines)")
    return "\n".join(preview)
