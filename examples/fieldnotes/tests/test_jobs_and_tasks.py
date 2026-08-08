import asyncio
from pathlib import Path

import aiosqlite
from app.features.knowledge.schemas import ReindexSourcesInput, SaveSource
from app.features.knowledge.tasks import tasks
from app.infra.port_wiring import ensure_schema
from app.server.runtime import create_context, create_lifespan
from app.server.tasks import tasks as composed_tasks
from app.server.tools import create_user_tool_runner
from app.server.worker import drain, process_next
from app.shared.users import User
from tenchi.tasks import create_task_runner

ALICE = User(id="alice", name="Alice")


async def test_indexing_job_and_reindex_task_share_durable_outbox(
    tmp_path: Path,
) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    runner = create_user_tool_runner(database_path=database_path, user=ALICE)
    source = await runner.call(
        "sources.save",
        input=SaveSource(title="Notes", content="One searchable passage."),
    )

    assert await drain(database_path) == 1

    task_runner = create_task_runner(
        tasks=tasks,
        context_factory=create_context,
        lifespan=create_lifespan(database_path),
    )
    dry_run = await task_runner.run(
        "knowledge.reindex_sources",
        input=ReindexSourcesInput(dry_run=True),
    )
    assert dry_run.discovered == 1
    assert dry_run.enqueued == 0

    queued = await task_runner.run(
        "knowledge.reindex_sources",
        input=ReindexSourcesInput(dry_run=False),
    )
    assert queued.enqueued == 1
    assert await drain(database_path) == 1

    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT state, COUNT(*) FROM outbox GROUP BY state"
        )
        states = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
        cursor = await connection.execute(
            "SELECT COUNT(*) FROM passages WHERE source_id = ?",
            (source.id,),
        )
        passage_row = await cursor.fetchone()

    assert passage_row is not None
    passage_count = int(passage_row[0])

    assert states == {"delivered": 2}
    assert passage_count == 1
    assert composed_tasks.tasks[0].name == tasks.tasks[0].name


async def test_schema_creation_is_idempotent(tmp_path: Path) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    await ensure_schema(database_path)
    await ensure_schema(database_path)


async def test_concurrent_workers_claim_an_outbox_entry_once(tmp_path: Path) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    runner = create_user_tool_runner(database_path=database_path, user=ALICE)
    await runner.call(
        "sources.save",
        input=SaveSource(title="Notes", content="Index this once."),
    )

    processed = await asyncio.gather(
        process_next(database_path),
        process_next(database_path),
    )

    assert sorted(processed) == [False, True]
    async with aiosqlite.connect(database_path) as connection:
        cursor = await connection.execute(
            "SELECT state, COUNT(*) FROM outbox GROUP BY state"
        )
        states = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
        cursor = await connection.execute("SELECT COUNT(*) FROM passages")
        row = await cursor.fetchone()

    assert states == {"delivered": 1}
    assert row is not None
    assert int(row[0]) == 1
