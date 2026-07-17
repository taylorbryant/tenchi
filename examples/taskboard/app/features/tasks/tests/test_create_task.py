import pytest

from app.features.tasks.schemas import CreateTask, CreateTaskHeaders, TaskStatus
from app.features.tasks.use_cases.create_task import create_task
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from app.shared.errors import forbidden, idempotency_conflict, project_not_found
from app.shared.users import OwnerScope, User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


def create_headers(key: str = "create-task") -> CreateTaskHeaders:
    return CreateTaskHeaders(idempotency_key=key)


def make_context(user: User) -> AppContext:
    projects = MemoryProjectRepository()
    return AppContext(
        projects=projects,
        tasks=(tasks := MemoryTaskRepository(projects)),
        task_search=MemoryTaskSearch(projects, tasks),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=user,
    )


async def test_create_task_in_an_owned_project() -> None:
    context = make_context(ALICE)
    project = await context.projects.create(
        name="Launch", owner=OwnerScope(owner_id="alice")
    )

    task = await create_task(
        create_headers(),
        CreateTask(project_id=project.id, title="Ship it"),
        context,
    )

    assert task.project_id == project.id
    assert task.title == "Ship it"
    assert task.status == TaskStatus.TODO
    assert task.version == 1


async def test_create_task_rejects_missing_project() -> None:
    context = make_context(ALICE)

    with pytest.raises(AppError) as excinfo:
        await create_task(
            create_headers(), CreateTask(project_id="missing", title="x"), context
        )

    assert excinfo.value.definition == project_not_found


async def test_create_task_in_another_owners_project_is_forbidden() -> None:
    context = make_context(BOB)
    project = await context.projects.create(
        name="Launch", owner=OwnerScope(owner_id="alice")
    )

    with pytest.raises(AppError) as excinfo:
        await create_task(
            create_headers(), CreateTask(project_id=project.id, title="x"), context
        )

    assert excinfo.value.definition == forbidden


async def test_matching_retry_replays_the_original_task() -> None:
    context = make_context(ALICE)
    project = await context.projects.create(
        name="Launch", owner=OwnerScope(owner_id="alice")
    )
    request = CreateTask(project_id=project.id, title="Ship it")

    original = await create_task(create_headers(), request, context)
    updated = await context.tasks.save(
        original.model_copy(update={"title": "Renamed"}),
        expected_version=original.version,
    )
    replayed = await create_task(create_headers(), request, context)

    assert updated is not None
    assert updated.version == 2
    assert replayed == original
    _, total = await context.task_search.search(
        viewer=OwnerScope(owner_id=ALICE.id),
        project_id=project.id,
        status=None,
        limit=10,
        offset=0,
    )
    assert total == 1


async def test_reusing_a_key_for_different_input_is_a_conflict() -> None:
    context = make_context(ALICE)
    project = await context.projects.create(
        name="Launch", owner=OwnerScope(owner_id="alice")
    )
    await create_task(
        create_headers(), CreateTask(project_id=project.id, title="First"), context
    )

    with pytest.raises(AppError) as excinfo:
        await create_task(
            create_headers(),
            CreateTask(project_id=project.id, title="Different"),
            context,
        )

    assert excinfo.value.definition == idempotency_conflict
    _, total = await context.task_search.search(
        viewer=OwnerScope(owner_id=ALICE.id),
        project_id=project.id,
        status=None,
        limit=10,
        offset=0,
    )
    assert total == 1
