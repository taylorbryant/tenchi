"""The SQLite adapters against a real file, including the ownership join."""

from pathlib import Path

from app.features.tasks.schemas import TaskStatus
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
        await ports.tasks.save(first.model_copy(update={"status": TaskStatus.DONE}))

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
        await ports.tasks.save(task.model_copy(update={"status": TaskStatus.DOING}))

    async with open_request_ports(database) as ports:
        reloaded = await ports.tasks.get(task.id)
        assert reloaded is not None
        assert reloaded.status == TaskStatus.DOING
        assert await ports.projects.get(project.id) == project


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
