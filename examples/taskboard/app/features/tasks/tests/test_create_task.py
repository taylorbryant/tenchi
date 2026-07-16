import pytest

from app.features.tasks.schemas import CreateTask, TaskStatus
from app.features.tasks.use_cases.create_task import create_task
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from app.shared.errors import forbidden, project_not_found
from app.shared.users import OwnerScope, User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


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
        CreateTask(project_id=project.id, title="Ship it"), context
    )

    assert task.project_id == project.id
    assert task.title == "Ship it"
    assert task.status == TaskStatus.TODO
    assert task.version == 1


async def test_create_task_rejects_missing_project() -> None:
    context = make_context(ALICE)

    with pytest.raises(AppError) as excinfo:
        await create_task(CreateTask(project_id="missing", title="x"), context)

    assert excinfo.value.definition == project_not_found


async def test_create_task_in_another_owners_project_is_forbidden() -> None:
    context = make_context(BOB)
    project = await context.projects.create(
        name="Launch", owner=OwnerScope(owner_id="alice")
    )

    with pytest.raises(AppError) as excinfo:
        await create_task(CreateTask(project_id=project.id, title="x"), context)

    assert excinfo.value.definition == forbidden
