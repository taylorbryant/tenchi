from pathlib import Path

import aiosqlite

from app.features.projects.schemas import RepairProjectMembersResult
from app.features.projects.tasks import tasks
from app.infra.port_wiring import configure_connection, ensure_schema
from app.server.runtime import create_context, create_lifespan
from tenchi.tasks import create_task_runner


async def test_repair_project_members_dry_run_and_write(tmp_path: Path) -> None:
    database_path = str(tmp_path / "taskboard.db")
    await ensure_schema(database_path)
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        await connection.execute(
            "INSERT INTO projects (id, name, owner_id, member_ids) "
            "VALUES ('broken', 'Broken', 'alice', 'not-json')"
        )
        await connection.commit()

    runner = create_task_runner(
        tasks=tasks,
        context_factory=create_context,
        lifespan=create_lifespan(database_path),
    )

    dry_run = await runner.run(
        "projects.repair_members",
        input={"dry_run": True},
    )
    assert dry_run == RepairProjectMembersResult(
        scanned=1,
        repaired=1,
        dry_run=True,
    )

    repaired = await runner.run(
        "projects.repair_members",
        input={"dry_run": False},
    )
    assert repaired.repaired == 1

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT member_ids FROM projects WHERE id = 'broken'"
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "[]"
