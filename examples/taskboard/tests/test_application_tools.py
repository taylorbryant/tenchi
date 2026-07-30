"""Application tools reuse policies and reliability primitives without MCP."""

from pathlib import Path

import aiosqlite
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.features.projects.schemas import Project
from app.features.tasks.schemas import MoveTaskInput, TaskStatus
from app.infra.port_wiring import configure_connection, ensure_schema
from app.server.mcp import create_user_mcp_server
from app.server.tools import create_user_tool_runner, tools
from app.shared.users import User
from tenchi.errors import AppError
from tenchi.tools import tool_manifest

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


async def seed_taskboard(database_path: str) -> None:
    await ensure_schema(database_path)
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        await connection.execute(
            "INSERT INTO projects (id, name, owner_id, member_ids) "
            "VALUES ('project-1', 'Launch', 'alice', '[]')"
        )
        await connection.execute(
            "INSERT INTO tasks (id, project_id, title, status, version) "
            "VALUES ('task-1', 'project-1', 'Ship it', 'todo', 1)"
        )
        await connection.commit()


def test_registered_tool_manifest_is_deterministic_and_safe_by_default() -> None:
    manifest = tool_manifest(tools)

    assert [entry["name"] for entry in manifest["tools"]] == [
        "projects.search",
        "tasks.move",
    ]
    search, move = manifest["tools"]
    assert search["annotations"] == {
        "destructive": False,
        "idempotent": True,
        "open_world": False,
        "read_only": True,
    }
    assert move["annotations"] == {
        "destructive": True,
        "idempotent": True,
        "open_world": False,
        "read_only": False,
    }
    assert {error["code"] for error in move["errors"]} == {
        "FORBIDDEN",
        "IDEMPOTENCY_CONFLICT",
        "IDEMPOTENCY_IN_PROGRESS",
        "PRECONDITION_FAILED",
        "RATE_LIMITED",
        "TASK_NOT_FOUND",
        "UNAUTHORIZED",
    }


async def test_authenticated_tools_search_move_replay_and_enforce_quota(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "taskboard.db")
    await seed_taskboard(database_path)
    alice = create_user_tool_runner(database_path=database_path, user=ALICE)

    projects = await alice.call("projects.search")
    assert projects == [
        Project(
            id="project-1",
            name="Launch",
            owner_id="alice",
        )
    ]

    first_input = MoveTaskInput(
        task_id="task-1",
        status=TaskStatus.DOING,
        expected_version=1,
        idempotency_key="move-1",
    )
    first = await alice.call("tasks.move", input=first_input)
    replay = await alice.call("tasks.move", input=first_input)
    assert first == replay
    assert first.status == TaskStatus.DOING
    assert first.version == 2

    with pytest.raises(AppError) as conflict:
        await alice.call(
            "tasks.move",
            input=first_input.model_copy(
                update={
                    "status": TaskStatus.DONE,
                    "expected_version": 2,
                }
            ),
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    current = first
    for index in range(2, 6):
        current = await alice.call(
            "tasks.move",
            input=MoveTaskInput(
                task_id="task-1",
                status=(
                    TaskStatus.TODO
                    if current.status != TaskStatus.TODO
                    else TaskStatus.DOING
                ),
                expected_version=current.version,
                idempotency_key=f"move-{index}",
            ),
        )
    assert current.version == 6

    with pytest.raises(AppError) as limited:
        await alice.call(
            "tasks.move",
            input=MoveTaskInput(
                task_id="task-1",
                status=(
                    TaskStatus.TODO
                    if current.status != TaskStatus.TODO
                    else TaskStatus.DOING
                ),
                expected_version=current.version,
                idempotency_key="move-6",
            ),
        )
    assert limited.value.code == "RATE_LIMITED"

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT status, version FROM tasks WHERE id = 'task-1'"
        )
        row = await cursor.fetchone()
    assert row == (current.status.value, 6)


async def test_tool_use_cases_enforce_identity_and_ownership(tmp_path: Path) -> None:
    database_path = str(tmp_path / "taskboard.db")
    await seed_taskboard(database_path)
    bob = create_user_tool_runner(database_path=database_path, user=BOB)

    assert await bob.call("projects.search") == []
    with pytest.raises(AppError) as hidden:
        await bob.call(
            "tasks.move",
            input=MoveTaskInput(
                task_id="task-1",
                status=TaskStatus.DOING,
                expected_version=1,
                idempotency_key="bob-move",
            ),
        )
    assert hidden.value.code == "TASK_NOT_FOUND"


async def test_application_mcp_reuses_authenticated_tool_wiring(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "taskboard.db")
    await seed_taskboard(database_path)

    unapproved = create_user_mcp_server(
        database_path=database_path,
        user=ALICE,
    )
    async with create_connected_server_and_client_session(unapproved) as session:
        listed = await session.list_tools()
        projects = await session.call_tool("projects.search", {})
        denied = await session.call_tool(
            "tasks.move",
            {
                "task_id": "task-1",
                "status": "doing",
                "expected_version": 1,
                "idempotency_key": "mcp-move-1",
            },
        )

    assert [definition.name for definition in listed.tools] == [
        "projects.search",
        "tasks.move",
    ]
    assert projects.structuredContent is not None
    assert projects.structuredContent["result"][0]["name"] == "Launch"
    assert denied.structuredContent is not None
    assert denied.structuredContent["error"]["kind"] == "approval_required"

    approved = create_user_mcp_server(
        database_path=database_path,
        user=ALICE,
        approve=lambda principal, declaration, input_value: True,
    )
    async with create_connected_server_and_client_session(approved) as session:
        moved = await session.call_tool(
            "tasks.move",
            {
                "task_id": "task-1",
                "status": "doing",
                "expected_version": 1,
                "idempotency_key": "mcp-move-1",
            },
        )

    assert moved.structuredContent is not None
    assert moved.structuredContent["ok"] is True
    assert moved.structuredContent["result"]["status"] == "doing"
    assert moved.structuredContent["result"]["version"] == 2
