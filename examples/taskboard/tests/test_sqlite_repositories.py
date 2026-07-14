"""The SQLite adapters against a real file, including the ownership join."""

from pathlib import Path

from app.features.tasks.schemas import TaskStatus
from app.infra.port_wiring import ensure_schema, open_request_ports


async def test_projects_round_trip(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        created = await ports.projects.create(name="Launch", owner_id="alice")
        await ports.projects.create(name="Other", owner_id="bob")

        assert await ports.projects.get(created.id) == created
        assert await ports.projects.get("missing") is None
        assert await ports.projects.list_owned_by("alice") == [created]


async def test_task_search_joins_ownership_and_paginates(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        mine = await ports.projects.create(name="Mine", owner_id="alice")
        other = await ports.projects.create(name="Other", owner_id="bob")
        for index in range(5):
            await ports.tasks.create(project_id=mine.id, title=f"task {index}")
        await ports.tasks.create(project_id=other.id, title="not mine")

        items, total = await ports.tasks.search(
            owner_id="alice", project_id=None, status=None, limit=2, offset=3
        )
        assert total == 5
        assert [task.title for task in items] == ["task 3", "task 4"]

        first = (
            await ports.tasks.search(
                owner_id="alice", project_id=mine.id, status=None, limit=1, offset=0
            )
        )[0][0]
        await ports.tasks.save(first.model_copy(update={"status": TaskStatus.DONE}))

        done_items, done_total = await ports.tasks.search(
            owner_id="alice",
            project_id=None,
            status=TaskStatus.DONE,
            limit=10,
            offset=0,
        )
        assert done_total == 1
        assert done_items[0].id == first.id


async def test_committed_scopes_persist_across_connections(tmp_path: Path) -> None:
    database = str(tmp_path / "taskboard.db")
    await ensure_schema(database)

    async with open_request_ports(database) as ports:
        project = await ports.projects.create(name="Launch", owner_id="alice")
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
            await ports.projects.create(name="doomed", owner_id="alice")
            raise Boom
    except Boom:
        pass

    async with open_request_ports(database) as ports:
        assert await ports.projects.list_owned_by("alice") == []
