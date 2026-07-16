from app.features.tasks.schemas import ListTasksQuery, TaskStatus
from app.features.tasks.use_cases.list_tasks import list_tasks
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from app.shared.users import OwnerScope, User

ALICE = User(id="alice", name="Alice")


async def make_populated_context() -> AppContext:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    context = AppContext(
        projects=projects,
        tasks=tasks,
        task_search=MemoryTaskSearch(projects, tasks),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=ALICE,
    )

    mine = await projects.create(name="Mine", owner=OwnerScope(owner_id="alice"))
    other = await projects.create(name="Other", owner=OwnerScope(owner_id="bob"))
    for index in range(5):
        await tasks.create(project_id=mine.id, title=f"task {index}")
    await tasks.create(project_id=other.id, title="not mine")
    return context


async def test_list_tasks_scopes_to_the_current_user() -> None:
    context = await make_populated_context()

    page = await list_tasks(ListTasksQuery(), context)

    assert page.total == 5
    assert all(task.title.startswith("task") for task in page.items)


async def test_list_tasks_paginates_with_total() -> None:
    context = await make_populated_context()

    page = await list_tasks(ListTasksQuery(limit=2, offset=4), context)

    assert page.total == 5
    assert [task.title for task in page.items] == ["task 4"]
    assert page.limit == 2
    assert page.offset == 4


async def test_list_tasks_filters_by_status() -> None:
    context = await make_populated_context()
    first = (await list_tasks(ListTasksQuery(limit=1), context)).items[0]
    await context.tasks.save(
        first.model_copy(update={"status": TaskStatus.DONE}),
        expected_version=first.version,
    )

    done = await list_tasks(ListTasksQuery(status=TaskStatus.DONE), context)

    assert done.total == 1
    assert done.items[0].id == first.id


async def test_members_see_shared_project_tasks_in_the_list() -> None:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    shared = await projects.create(name="Shared", owner=OwnerScope(owner_id="bob"))
    await projects.save(shared.model_copy(update={"member_ids": ("alice",)}))
    await tasks.create(project_id=shared.id, title="shared task")

    alice = AppContext(
        projects=projects,
        tasks=tasks,
        task_search=MemoryTaskSearch(projects, tasks),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=ALICE,
    )

    page = await list_tasks(ListTasksQuery(), alice)

    # Listing agrees with get: what a member can fetch, a member can list.
    assert [task.title for task in page.items] == ["shared task"]
