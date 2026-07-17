"""The SQLite adapters against a real file, including the ownership join."""

import asyncio
from pathlib import Path

import pytest

from app.features.tasks.schemas import Task, TaskStatus
from app.infra.port_wiring import ensure_schema, open_request_ports
from app.shared.users import OwnerScope


async def test_projects_round_trip(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        created = await ports.projects.create(
            name="Launch", owner=OwnerScope(owner_id="alice")
        )
        await ports.projects.create(name="Other", owner=OwnerScope(owner_id="bob"))

        assert await ports.projects.get(created.id) == created
        assert await ports.projects.get("missing") is None
        assert await ports.projects.list_owned_by(OwnerScope(owner_id="alice")) == [
            created
        ]


async def test_task_search_joins_ownership_and_paginates(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    # Setup commits first: task_search runs on the read connection, which
    # — like a real replica — sees only committed data, never the
    # primary's open transaction.
    async with open_request_ports(database) as ports:
        mine = await ports.projects.create(
            name="Mine", owner=OwnerScope(owner_id="alice")
        )
        other = await ports.projects.create(
            name="Other", owner=OwnerScope(owner_id="bob")
        )
        for index in range(5):
            await ports.tasks.create(project_id=mine.id, title=f"task {index}")
        await ports.tasks.create(project_id=other.id, title="not mine")

    async with open_request_ports(database) as ports:
        items, total = await ports.task_search.search(
            viewer=OwnerScope(owner_id="alice"),
            project_id=None,
            status=None,
            limit=2,
            offset=3,
        )
        assert total == 5
        assert [task.title for task in items] == ["task 3", "task 4"]

        first = items[0]
        await ports.tasks.save(
            first.model_copy(update={"status": TaskStatus.DONE}),
            expected_version=first.version,
        )

    async with open_request_ports(database) as ports:
        done_items, done_total = await ports.task_search.search(
            viewer=OwnerScope(owner_id="alice"),
            project_id=None,
            status=TaskStatus.DONE,
            limit=10,
            offset=0,
        )
        assert done_total == 1
        assert done_items[0].id == first.id


async def test_read_connection_sees_only_committed_data(tmp_path: Path) -> None:
    """The replica-semantics property itself: an uncommitted write on the
    primary is invisible to task_search until the scope commits."""
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        mine = await ports.projects.create(
            name="Mine", owner=OwnerScope(owner_id="alice")
        )
        await ports.tasks.create(project_id=mine.id, title="pending")

        _, total = await ports.task_search.search(
            viewer=OwnerScope(owner_id="alice"),
            project_id=None,
            status=None,
            limit=10,
            offset=0,
        )
        assert total == 0  # not committed yet: the read side can't see it

        # Strong reads stay on the primary and DO see the open transaction.
        assert await ports.projects.get(mine.id) is not None


async def test_read_connection_rejects_writes(tmp_path: Path) -> None:
    """A wiring mistake that hands the read connection to a write path
    must fail loudly, exactly as a real replica would reject the write."""
    import sqlite3

    import pytest

    from app.infra.port_wiring import open_read_connection

    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_read_connection(database) as reader:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            await reader.execute("INSERT INTO projects VALUES ('x', 'X', 'a', '[]')")


async def test_committed_scopes_persist_across_connections(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        project = await ports.projects.create(
            name="Launch", owner=OwnerScope(owner_id="alice")
        )
        task = await ports.tasks.create(project_id=project.id, title="persist me")
        saved = await ports.tasks.save(
            task.model_copy(update={"status": TaskStatus.DOING}),
            expected_version=task.version,
        )
        assert saved is not None
        assert saved.version == 2

    async with open_request_ports(database) as ports:
        reloaded = await ports.tasks.get(task.id)
        assert reloaded is not None
        assert reloaded.status == TaskStatus.DOING
        assert reloaded.version == 2
        assert await ports.projects.get(project.id) == project


async def test_task_save_is_an_atomic_compare_and_swap(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        project = await ports.projects.create(
            name="Launch", owner=OwnerScope(owner_id="alice")
        )
        original = await ports.tasks.create(project_id=project.id, title="original")
        stale = original.model_copy()

        first = await ports.tasks.save(
            original.model_copy(update={"title": "first writer"}),
            expected_version=original.version,
        )
        assert first is not None
        assert first.version == 2

        rejected = await ports.tasks.save(
            stale.model_copy(update={"title": "stale writer"}),
            expected_version=stale.version,
        )
        assert rejected is None
        assert await ports.tasks.get(original.id) == first


async def test_concurrent_task_saves_have_exactly_one_winner(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        project = await ports.projects.create(
            name="Launch", owner=OwnerScope(owner_id="alice")
        )
        original = await ports.tasks.create(project_id=project.id, title="original")

    async def save(title: str) -> Task | None:
        async with open_request_ports(database) as ports:
            return await ports.tasks.save(
                original.model_copy(update={"title": title}),
                expected_version=original.version,
            )

    results = await asyncio.gather(save("first writer"), save("second writer"))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].version == 2
    async with open_request_ports(database) as ports:
        assert await ports.tasks.get(original.id) == winners[0]


async def test_idempotent_task_create_replays_one_concurrent_insert(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    owner = OwnerScope(owner_id="alice")
    async with open_request_ports(database) as ports:
        project = await ports.projects.create(name="Launch", owner=owner)

    async def create() -> Task | None:
        async with open_request_ports(database) as ports:
            return await ports.tasks.create_idempotent(
                project_id=project.id,
                title="Ship it",
                owner=owner,
                idempotency_key="concurrent-create",
                request_fingerprint="matching-input",
            )

    results = await asyncio.gather(*(create() for _ in range(8)))

    assert all(result is not None for result in results)
    assert results == [results[0]] * len(results)
    async with open_request_ports(database) as ports:
        _, total = await ports.task_search.search(
            viewer=owner,
            project_id=project.id,
            status=None,
            limit=10,
            offset=0,
        )
    assert total == 1


async def test_idempotency_key_rejects_different_input(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    owner = OwnerScope(owner_id="alice")
    async with open_request_ports(database) as ports:
        project = await ports.projects.create(name="Launch", owner=owner)
        original = await ports.tasks.create_idempotent(
            project_id=project.id,
            title="Original",
            owner=owner,
            idempotency_key="reused-key",
            request_fingerprint="original-input",
        )

    async with open_request_ports(database) as ports:
        assert original is not None
        updated = await ports.tasks.save(
            original.model_copy(update={"title": "Renamed"}),
            expected_version=original.version,
        )
        replayed = await ports.tasks.create_idempotent(
            project_id=project.id,
            title="Original",
            owner=owner,
            idempotency_key="reused-key",
            request_fingerprint="original-input",
        )
        conflict = await ports.tasks.create_idempotent(
            project_id=project.id,
            title="Different",
            owner=owner,
            idempotency_key="reused-key",
            request_fingerprint="different-input",
        )
        _, total = await ports.task_search.search(
            viewer=owner,
            project_id=project.id,
            status=None,
            limit=10,
            offset=0,
        )

    assert updated is not None
    assert updated.version == 2
    assert replayed == original
    assert conflict is None
    assert total == 1


async def test_idempotency_keys_are_scoped_to_the_authenticated_owner(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    alice = OwnerScope(owner_id="alice")
    bob = OwnerScope(owner_id="bob")
    async with open_request_ports(database) as ports:
        alice_project = await ports.projects.create(name="Alice", owner=alice)
        bob_project = await ports.projects.create(name="Bob", owner=bob)
        alice_task = await ports.tasks.create_idempotent(
            project_id=alice_project.id,
            title="Alice task",
            owner=alice,
            idempotency_key="shared-client-key",
            request_fingerprint="alice-input",
        )
        bob_task = await ports.tasks.create_idempotent(
            project_id=bob_project.id,
            title="Bob task",
            owner=bob,
            idempotency_key="shared-client-key",
            request_fingerprint="bob-input",
        )

    assert alice_task is not None
    assert bob_task is not None
    assert alice_task.id != bob_task.id


async def test_failed_transaction_does_not_consume_idempotency_key(
    tmp_path: Path,
) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)
    owner = OwnerScope(owner_id="alice")
    async with open_request_ports(database) as ports:
        project = await ports.projects.create(name="Launch", owner=owner)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with open_request_ports(database) as ports:
            doomed = await ports.tasks.create_idempotent(
                project_id=project.id,
                title="Ship it",
                owner=owner,
                idempotency_key="retry-after-rollback",
                request_fingerprint="matching-input",
            )
            assert doomed is not None
            raise Boom

    async with open_request_ports(database) as ports:
        retried = await ports.tasks.create_idempotent(
            project_id=project.id,
            title="Ship it",
            owner=owner,
            idempotency_key="retry-after-rollback",
            request_fingerprint="matching-input",
        )

    async with open_request_ports(database) as ports:
        _, total = await ports.task_search.search(
            viewer=owner,
            project_id=project.id,
            status=None,
            limit=10,
            offset=0,
        )

    assert retried is not None
    assert total == 1


async def test_ensure_schema_migrates_existing_task_tables(tmp_path: Path) -> None:
    import aiosqlite

    database = str(tmp_path / "old-taskboard.db")
    async with aiosqlite.connect(database) as connection:
        await connection.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, "
            "title TEXT NOT NULL, status TEXT NOT NULL)"
        )
        await connection.execute(
            "INSERT INTO tasks (id, project_id, title, status) VALUES (?, ?, ?, ?)",
            ("legacy-task", "legacy-project", "Existing", TaskStatus.TODO.value),
        )
        await connection.commit()

    # The ASGI app and worker both initialize the schema at startup. An
    # existing database must migrate safely even when they start together.
    await asyncio.gather(*(ensure_schema(database) for _ in range(8)))
    await ensure_schema(database)

    async with aiosqlite.connect(database) as connection:
        columns = {
            str(row[1])
            for row in await (
                await connection.execute("PRAGMA table_info(tasks)")
            ).fetchall()
        }
    assert "version" in columns

    async with open_request_ports(database) as ports:
        migrated = await ports.tasks.get("legacy-task")
        assert migrated is not None
        assert migrated.version == 1
        saved = await ports.tasks.save(
            migrated.model_copy(update={"title": "Updated"}),
            expected_version=migrated.version,
        )
        assert saved is not None
        assert saved.title == "Updated"
        assert saved.version == 2


async def test_failed_scope_rolls_back(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    class Boom(Exception):
        pass

    try:
        async with open_request_ports(database) as ports:
            await ports.projects.create(
                name="doomed", owner=OwnerScope(owner_id="alice")
            )
            raise Boom
    except Boom:
        pass

    async with open_request_ports(database) as ports:
        assert await ports.projects.list_owned_by(OwnerScope(owner_id="alice")) == []
