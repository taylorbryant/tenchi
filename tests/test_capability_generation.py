"""Exercise explicit feature selection and create-only tools adoption."""

import errno
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import IO, Any

import pytest
from mcp.client import Client

from tenchi._capability_operations import make_capability_result
from tenchi._cli_operations import make_feature_result, write_files
from tenchi._mcp_server import McpServerOptions, build_mcp_server
from tenchi.cli import main
from tenchi.scaffold import app_files, capability_files


def _contents(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


@pytest.mark.parametrize("full", [False, True])
def test_feature_capabilities_are_explicit_and_order_independent(
    tmp_path: Path, full: bool
) -> None:
    write_files(tmp_path, app_files("sample", full=full))
    before = _contents(tmp_path)
    default = make_feature_result(tmp_path, name="invoices", dry_run=True)
    selected = make_feature_result(
        tmp_path, name="invoices", dry_run=True, capabilities=["tools", "jobs", "tools"]
    )
    reordered = make_feature_result(
        tmp_path, name="invoices", dry_run=True, capabilities=["jobs", "tools"]
    )
    assert default.ok and selected.ok
    assert selected == reordered
    assert set(selected.files) - set(default.files) == {
        "app/features/invoices/jobs.py",
        "app/features/invoices/tools.py",
    }
    assert not any(
        path.endswith(("tasks.py", "jobs.py", "tools.py", "evaluations.py"))
        for path in default.files
    )
    assert any("job_handler()" in step for step in selected.next_steps)
    assert any("make capability tools" in step for step in selected.next_steps) == (
        not full
    )
    assert _contents(tmp_path) == before


def test_feature_wiring_names_an_existing_composition_package(tmp_path: Path) -> None:
    write_files(tmp_path, app_files("sample"))
    write_files(tmp_path, {"app/server/tools/__init__.py": ""})
    result = make_feature_result(
        tmp_path, name="invoices", dry_run=True, capabilities=["tools"]
    )
    assert result.ok
    assert result.next_steps[-1] == (
        "Compose app.features.invoices.tools in app/server/tools/__init__.py"
    )


def test_tools_adoption_preview_and_write_preserve_existing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_files(tmp_path, app_files("sample"))
    monkeypatch.chdir(tmp_path)
    before = _contents(tmp_path)
    assert main(["make", "capability", "tools", "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["artifact"] == "capability"
    assert preview["feature"] is None
    assert preview["dry_run"] is True
    assert _contents(tmp_path) == before
    assert main(["make", "capability", "tools", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created == {**preview, "dry_run": False}
    after = _contents(tmp_path)
    assert set(after) - set(before) == set(capability_files("tools"))
    assert all(after[path] == content for path, content in before.items())
    full = app_files("sample", full=True)
    for path in created["files"]:
        assert after[path].decode() == full[path]
    assert any("tools = true inside [verify]" in step for step in created["next_steps"])
    assert any("--diff tools.json" in step for step in created["next_steps"])
    assert any("--base-ref <ref>" in step for step in created["next_steps"])
    assert main(["make", "capability", "tools", "--json"]) == 1
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["ok"] is False
    assert all(path in conflict["error"] for path in created["files"])
    assert _contents(tmp_path) == after


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize(
    "conflict",
    [
        "app/server/tools.py",
        "tools.json",
        "tests/test_tool_snapshot.py",
        "app/server/tools",
    ],
)
def test_tools_adoption_refuses_partial_layouts(
    tmp_path: Path, conflict: str, dry_run: bool
) -> None:
    write_files(tmp_path, app_files("sample"))
    path = tmp_path / conflict
    if conflict == "app/server/tools":
        path.mkdir()
        (path / "__init__.py").write_text("# existing package\n")
    else:
        path.write_text("existing application content\n")
    before = _contents(tmp_path)
    result = make_capability_result(tmp_path, name="tools", dry_run=dry_run)
    assert not result.ok
    assert result.error is not None and conflict in result.error
    assert _contents(tmp_path) == before


@pytest.mark.parametrize("relative", ["app", "app/server", "tests", "tools.json"])
def test_tools_adoption_refuses_symlink_paths(tmp_path: Path, relative: str) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "app/server").mkdir(parents=True)
    path = root / relative
    if path.is_dir():
        if relative == "app":
            (path / "server").rmdir()
        path.rmdir()
    # Include a dangling destination link as well as existing directory links.
    path.symlink_to(outside / "missing" if relative == "tools.json" else outside)
    for dry_run in (True, False):
        result = make_capability_result(root, name="tools", dry_run=dry_run)
        assert not result.ok
        assert result.error is not None and relative in result.error
    assert list(outside.iterdir()) == []


def test_tools_adoption_rolls_back_failed_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_files(tmp_path, app_files("sample"))
    before = _contents(tmp_path)
    original_open = Path.open

    def fail_snapshot(
        destination: Path, mode: str = "r", *args: Any, **kwargs: Any
    ) -> IO[Any]:
        if destination.name == "tools.json" and mode == "x":
            raise OSError("simulated write failure")
        return original_open(destination, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_snapshot)
    result = make_capability_result(tmp_path, name="tools", dry_run=False)
    assert not result.ok
    assert _contents(tmp_path) == before


def test_tools_adoption_preserves_a_concurrent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_files(tmp_path, app_files("sample"))
    before = _contents(tmp_path)
    original_open = Path.open

    def create_conflict(
        destination: Path, mode: str = "r", *args: Any, **kwargs: Any
    ) -> IO[Any]:
        if destination.name == "tools.json" and mode == "x":
            destination.write_text("concurrent content\n")
        return original_open(destination, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", create_conflict)
    result = make_capability_result(tmp_path, name="tools", dry_run=False)
    assert not result.ok
    assert _contents(tmp_path) == {**before, "tools.json": b"concurrent content\n"}


def test_tools_adoption_removes_a_partially_written_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_files(tmp_path, app_files("sample"))
    before = _contents(tmp_path)
    original_open = Path.open

    def fail_during_write(
        destination: Path, mode: str = "r", *args: Any, **kwargs: Any
    ) -> IO[Any]:
        output = original_open(destination, mode, *args, **kwargs)
        if destination.name == "tools.json" and mode == "x":
            original_write = output.write

            def write_prefix(content: str) -> int:
                original_write(content[:5])
                output.flush()
                raise OSError(errno.ENOSPC, "simulated disk full")

            monkeypatch.setattr(output, "write", write_prefix)
        return output

    monkeypatch.setattr(Path, "open", fail_during_write)
    result = make_capability_result(tmp_path, name="tools", dry_run=False)
    assert not result.ok
    assert result.error is not None and "simulated disk full" in result.error
    assert _contents(tmp_path) == before


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory permissions")
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("relative", ["tests", "app/server"])
def test_inaccessible_capability_paths_return_one_json_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dry_run: bool,
    relative: str,
) -> None:
    write_files(tmp_path, app_files("sample"))
    monkeypatch.chdir(tmp_path)
    before = _contents(tmp_path)
    blocked = tmp_path / relative
    original_mode = stat.S_IMODE(blocked.stat().st_mode)
    blocked.chmod(0)
    try:
        if os.access(blocked, os.X_OK):
            pytest.skip("the current user bypasses directory permissions")
        flags = ["--dry-run"] if dry_run else []
        assert main(["make", "capability", "tools", *flags, "--json"]) == 1
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["ok"] is False
        assert result["files"] == []
        assert "could not inspect application paths" in result["error"]
        assert captured.err == ""
    finally:
        blocked.chmod(original_mode)
    assert _contents(tmp_path) == before


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX directory permissions")
@pytest.mark.parametrize(
    "arguments",
    [
        ["capability", "tools"],
        ["feature", "notes"],
        ["use-case", "todos", "review_todos"],
    ],
)
def test_generators_do_not_require_a_writable_application_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    root = tmp_path / "project"
    write_files(root, app_files("sample"))
    monkeypatch.chdir(root)
    original_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    tmp_path.chmod(0o555)
    try:
        if os.access(tmp_path, os.W_OK):
            pytest.skip("the current user bypasses directory permissions")
        assert main(["make", *arguments, "--json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["ok"] is True
        assert all((root / path).is_file() for path in result["files"])
        assert sorted(path.name for path in tmp_path.iterdir()) == ["project"]
    finally:
        tmp_path.chmod(original_mode)


@pytest.mark.parametrize(
    "arguments",
    [
        ["capability", "tools"],
        ["feature", "notes"],
        ["use-case", "todos", "review_todos"],
    ],
)
def test_generators_work_without_filesystem_hard_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    write_files(tmp_path, app_files("sample"))
    monkeypatch.chdir(tmp_path)

    def unsupported_link(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOTSUP, "hard links are unavailable")

    monkeypatch.setattr(os, "link", unsupported_link)
    assert main(["make", *arguments, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert all((tmp_path / path).is_file() for path in result["files"])


async def test_mcp_capability_preview_returns_structured_filesystem_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_files(tmp_path, app_files("sample"))
    before = _contents(tmp_path)
    original_lstat = Path.lstat

    def deny_snapshot(path: Path) -> os.stat_result:
        if path == tmp_path / "tools.json":
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_snapshot)
    server = build_mcp_server(McpServerOptions(tmp_path))
    async with Client(server) as session:
        result = await session.call_tool(
            "make_preview", {"artifact": "capability", "name": "tools"}
        )
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["ok"] is False
    assert "could not inspect application paths" in result.structured_content["error"]
    assert _contents(tmp_path) == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["make", "capability", "jobs"],
        ["make", "feature", "notes", "--with", "unknown"],
        ["make", "capability", "tools", "--with", "tools"],
    ],
)
def test_invalid_selection_is_one_json_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main([*arguments, "--json"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["code"] == "TENCHI_CLI_INVALID_ARGUMENTS"
    assert captured.err == ""
    assert list(tmp_path.iterdir()) == []


async def test_mcp_previews_match_shared_operations_without_importing_app(
    tmp_path: Path,
) -> None:
    write_files(tmp_path, app_files("sample"))
    (tmp_path / "app/__init__.py").write_text("raise RuntimeError('must not import')\n")
    before = _contents(tmp_path)
    server = build_mcp_server(McpServerOptions(tmp_path))
    async with Client(server) as session:
        feature = await session.call_tool(
            "make_preview",
            {
                "artifact": "feature",
                "name": "invoices",
                "capabilities": ["jobs", "tools"],
            },
        )
        capability = await session.call_tool(
            "make_preview", {"artifact": "capability", "name": "tools"}
        )
        invalid_arguments: tuple[dict[str, object], ...] = (
            {"artifact": "capability", "name": "tools", "feature": "invoices"},
            {"artifact": "capability", "name": "tools", "capabilities": []},
            {"artifact": "use-case", "name": "create", "capabilities": ["tools"]},
            {"artifact": "feature", "name": "invoices", "capabilities": ["unknown"]},
        )
        for arguments in invalid_arguments:
            invalid = await session.call_tool("make_preview", arguments)
            assert invalid.is_error
    assert not feature.is_error and not capability.is_error
    assert (
        feature.structured_content
        == make_feature_result(
            tmp_path, name="invoices", dry_run=True, capabilities=["jobs", "tools"]
        ).as_dict()
    )
    assert (
        capability.structured_content
        == make_capability_result(tmp_path, name="tools", dry_run=True).as_dict()
    )
    assert _contents(tmp_path) == before


def test_adopted_tools_pass_check_and_historical_verification(tmp_path: Path) -> None:
    write_files(tmp_path, app_files("sample"))
    for arguments in (
        ["init", "--quiet"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.com"],
        ["add", "."],
        ["commit", "--quiet", "-m", "Before tools adoption"],
    ):
        subprocess.run(
            ["git", *arguments], cwd=tmp_path, check=True, capture_output=True
        )
    result = make_capability_result(tmp_path, name="tools", dry_run=False)
    assert result.ok
    # The user completes the printed policy step; the generator never edits it.
    policy = tmp_path / "tenchi.toml"
    policy.write_text(policy.read_text() + "tools = true\n")
    # Follow the composition steps with a real generated use case. Snapshot
    # drift must be reviewed and accepted before verification can pass.
    write_files(
        tmp_path,
        {
            "app/features/todos/tools.py": """\
from tenchi.tools import tool, tool_group, tool_handler

from .schemas import CreateTodo, Todo
from .use_cases.create_todo import create_todo

create_todo_tool = tool(
    "todos.create", request=CreateTodo, result=Todo, description="Create a todo."
)
tools = tool_group(tool_handler(create_todo_tool, create_todo))
""",
            "app/server/tools.py": """\
from tenchi.tools import tool_group

from app.features.todos.tools import tools as todo_tools

tools = tool_group(todo_tools)
""",
        },
    )
    for arguments, exit_code in (
        (["tools", "--check", "tools.json"], 1),
        (["tools", "--diff", "tools.json"], 0),
        (["tools", "--write", "tools.json"], 0),
    ):
        snapshot = subprocess.run(
            [sys.executable, "-m", "tenchi.cli", *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert snapshot.returncode == exit_code, snapshot.stdout + snapshot.stderr
    verified = subprocess.run(
        [sys.executable, "-m", "tenchi.cli", "verify", "--base-ref", "HEAD", "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    receipt = json.loads(verified.stdout)
    assert receipt["ok"]
    assert "tool manifest baseline" in verified.stdout
