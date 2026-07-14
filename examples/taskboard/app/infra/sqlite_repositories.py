"""SQLite implementations of the taskboard ports.

Both repositories share the request-scoped connection owned by
:func:`app.infra.port_wiring.open_request_ports`. Writes participate in
the request transaction; the scope commits on success and the connection
rolls back uncommitted work when closed after an error.
"""

import json
from typing import Any
from uuid import uuid4

import aiosqlite

from app.features.projects.schemas import Project
from app.features.tasks.schemas import Task, TaskStatus
from app.shared.users import OwnerScope

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    member_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects (id),
    title TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class SqliteProjectRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create(self, *, name: str, owner: OwnerScope) -> Project:
        project = Project(id=uuid4().hex, name=name, owner_id=owner.owner_id)
        await self._connection.execute(
            "INSERT INTO projects (id, name, owner_id, member_ids) VALUES (?, ?, ?, ?)",
            (project.id, project.name, project.owner_id, "[]"),
        )
        return project

    async def get(self, project_id: str) -> Project | None:
        cursor = await self._connection.execute(
            "SELECT id, name, owner_id, member_ids FROM projects WHERE id = ?",
            (project_id,),
        )
        row = await cursor.fetchone()
        return _row_to_project(row) if row is not None else None

    async def save(self, project: Project) -> Project:
        await self._connection.execute(
            "UPDATE projects SET name = ?, member_ids = ? WHERE id = ?",
            (project.name, json.dumps(list(project.member_ids)), project.id),
        )
        return project

    async def list_owned_by(self, owner: OwnerScope) -> list[Project]:
        cursor = await self._connection.execute(
            "SELECT id, name, owner_id, member_ids FROM projects "
            "WHERE owner_id = ? ORDER BY rowid",
            (owner.owner_id,),
        )
        return [_row_to_project(row) for row in await cursor.fetchall()]


class SqliteTaskRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def create(self, *, project_id: str, title: str) -> Task:
        task = Task(
            id=uuid4().hex,
            project_id=project_id,
            title=title,
            status=TaskStatus.TODO,
        )
        await self._connection.execute(
            "INSERT INTO tasks (id, project_id, title, status) VALUES (?, ?, ?, ?)",
            (task.id, task.project_id, task.title, task.status.value),
        )
        return task

    async def get(self, task_id: str) -> Task | None:
        cursor = await self._connection.execute(
            "SELECT id, project_id, title, status FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = await cursor.fetchone()
        return _row_to_task(row) if row is not None else None

    async def save(self, task: Task) -> Task:
        await self._connection.execute(
            "UPDATE tasks SET title = ?, status = ? WHERE id = ?",
            (task.title, task.status.value, task.id),
        )
        return task

    async def search(
        self,
        *,
        owner: OwnerScope,
        project_id: str | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        conditions = ["projects.owner_id = ?"]
        values: list[Any] = [owner.owner_id]
        if project_id is not None:
            conditions.append("tasks.project_id = ?")
            values.append(project_id)
        if status is not None:
            conditions.append("tasks.status = ?")
            values.append(status.value)
        where = " AND ".join(conditions)
        base = (
            f"FROM tasks JOIN projects ON projects.id = tasks.project_id WHERE {where}"
        )

        cursor = await self._connection.execute(
            f"SELECT COUNT(*) {base}", tuple(values)
        )
        count_row = await cursor.fetchone()
        total = int(count_row[0]) if count_row is not None else 0

        cursor = await self._connection.execute(
            f"SELECT tasks.id, tasks.project_id, tasks.title, tasks.status {base} "
            "ORDER BY tasks.rowid LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )
        items = [_row_to_task(row) for row in await cursor.fetchall()]
        return items, total


def _row_to_project(row: Any) -> Project:
    return Project(
        id=row[0],
        name=row[1],
        owner_id=row[2],
        member_ids=tuple(json.loads(row[3])),
    )


def _row_to_task(row: Any) -> Task:
    return Task(id=row[0], project_id=row[1], title=row[2], status=TaskStatus(row[3]))
