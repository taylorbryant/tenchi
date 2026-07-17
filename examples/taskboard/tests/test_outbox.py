"""The transactional outbox, end to end.

Enqueued jobs commit and roll back with the state change that announced
them; the worker delivers valid jobs through an ordinary use case and
dead-letters everything else.
"""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import pytest
from starlette.applications import Starlette

from app.features.projects.routes import routes as project_routes
from app.features.projects.schemas import MemberAdded
from app.infra.port_wiring import ensure_schema, open_request_ports
from app.infra.sqlite_repositories import SqliteNotificationLog, SqliteOutbox
from app.server import worker
from app.server.context import AppContext
from app.server.worker import drain
from app.shared.errors import forbidden
from app.shared.users import User
from tenchi.contracts import contract
from tenchi.errors import AppError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import create_app
from tenchi.testing import open_http

ALICE = User(id="alice", name="Alice")

glitch = ErrorDef(code="GLITCH", status=409, message="Glitched after enqueueing")
glitch_contract = contract(method="POST", path="/glitch", errors=(glitch,))


async def enqueue_then_fail(context: AppContext) -> None:
    await context.outbox.enqueue(job="member_added", payload={"doomed": True})
    raise AppError(glitch)


def make_app(database_path: str) -> Starlette:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        await ensure_schema(database_path)
        yield database_path

    @asynccontextmanager
    async def create_context(path: str) -> AsyncGenerator[AppContext]:
        async with open_request_ports(path) as ports:
            yield AppContext(
                projects=ports.projects,
                tasks=ports.tasks,
                task_search=ports.task_search,
                outbox=ports.outbox,
                notifications=ports.notifications,
                user=ALICE,
            )

    return create_app(
        routes=route_group(project_routes, route(glitch_contract, enqueue_then_fail)),
        context_factory=create_context,
        lifespan=lifespan,
    )


async def outbox_rows(database_path: str) -> list[tuple[str, str, int, str | None]]:
    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT job, payload, processed, error FROM outbox ORDER BY id"
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def add_member_over_http(database_path: str) -> str:
    """Create a project and add bob to it; returns the project id."""
    app = make_app(database_path)
    async with open_http(app) as http:
        created = await http.post("/projects", json={"name": "Launch"})
        assert created.status_code == 201
        project_id = created.json()["id"]
        added = await http.post(
            f"/projects/{project_id}/members", json={"user_id": "bob"}
        )
        assert added.status_code == 201
    return project_id


async def test_the_job_commits_with_the_membership_change(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")

    project_id = await add_member_over_http(database)

    rows = await outbox_rows(database)
    assert len(rows) == 1
    job, payload, processed, error = rows[0]
    assert job == "member_added"
    assert json.loads(payload) == {
        "project_id": project_id,
        "project_name": "Launch",
        "user_id": "bob",
    }
    assert processed == 0 and error is None


async def test_rollback_discards_the_enqueued_job(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    app = make_app(database)

    async with open_http(app) as http:
        response = await http.post("/glitch")
        assert response.status_code == 409

    assert await outbox_rows(database) == []


async def test_the_worker_delivers_the_notification(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await add_member_over_http(database)

    assert await drain(database) == 1

    async with aiosqlite.connect(database) as connection:
        messages = await SqliteNotificationLog(connection).list_for("bob")
    assert messages == ["You were added to project 'Launch'"]
    (row,) = await outbox_rows(database)
    assert row[2] == 1 and row[3] is None  # processed, no error


async def test_unknown_jobs_are_dead_lettered(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    async with aiosqlite.connect(database) as connection:
        await SqliteOutbox(connection).enqueue(job="bogus", payload={})
        await connection.commit()

    assert await drain(database) == 1

    (row,) = await outbox_rows(database)
    assert row[2] == 1 and row[3] == "unknown job 'bogus'"


async def test_malformed_payloads_are_dead_lettered(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    async with aiosqlite.connect(database) as connection:
        await SqliteOutbox(connection).enqueue(
            job="member_added", payload={"wrong": "shape"}
        )
        await connection.commit()

    assert await drain(database) == 1

    (row,) = await outbox_rows(database)
    assert row[2] == 1 and row[3] is not None and "validation error" in row[3]
    async with aiosqlite.connect(database) as connection:
        assert await SqliteNotificationLog(connection).list_for("bob") == []


async def test_deterministic_failures_dead_letter_instead_of_starving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poison job must not block the jobs queued behind it."""
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async def rejects(request: MemberAdded, context: AppContext) -> None:
        raise AppError(forbidden)

    monkeypatch.setitem(worker.JOB_HANDLERS, "poison", rejects)
    payload = {"project_id": "p", "project_name": "P", "user_id": "bob"}
    async with aiosqlite.connect(database) as connection:
        outbox = SqliteOutbox(connection)
        await outbox.enqueue(job="poison", payload=payload)
        await outbox.enqueue(job="member_added", payload=payload)
        await connection.commit()

    assert await drain(database) == 2

    rows = await outbox_rows(database)
    assert [row[2] for row in rows] == [1, 1]  # both settled
    assert rows[0][3] is not None and "FORBIDDEN" in rows[0][3]
    assert rows[1][3] is None  # the job behind the poison one delivered
    async with aiosqlite.connect(database) as connection:
        assert await SqliteNotificationLog(connection).list_for("bob") == [
            "You were added to project 'P'"
        ]


async def test_miswired_handlers_dead_letter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async def no_request_param(context: AppContext) -> None:
        return None

    monkeypatch.setitem(worker.JOB_HANDLERS, "miswired", no_request_param)
    async with aiosqlite.connect(database) as connection:
        await SqliteOutbox(connection).enqueue(job="miswired", payload={"x": 1})
        await connection.commit()

    assert await drain(database) == 1

    (row,) = await outbox_rows(database)
    assert row[2] == 1 and row[3] is not None
    assert "request" in row[3]


async def test_dead_lettering_rolls_back_partial_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A use case that writes and then fails deterministically must not
    commit the write alongside the dead-letter record."""
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async def writes_then_rejects(request: MemberAdded, context: AppContext) -> None:
        await context.notifications.record(user_id="bob", message="half-done")
        raise AppError(forbidden)

    monkeypatch.setitem(worker.JOB_HANDLERS, "half", writes_then_rejects)
    async with aiosqlite.connect(database) as connection:
        await SqliteOutbox(connection).enqueue(
            job="half",
            payload={"project_id": "p", "project_name": "P", "user_id": "bob"},
        )
        await connection.commit()

    assert await drain(database) == 1

    (row,) = await outbox_rows(database)
    assert row[2] == 1 and row[3] is not None
    async with aiosqlite.connect(database) as connection:
        assert await SqliteNotificationLog(connection).list_for("bob") == []
