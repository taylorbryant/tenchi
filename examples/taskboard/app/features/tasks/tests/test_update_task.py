import pytest

from app.features.tasks.schemas import GetTaskParams, TaskStatus, UpdateTask
from app.features.tasks.use_cases.update_task import update_task
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.server.context import AppContext
from app.shared.errors import task_not_found
from app.shared.users import OwnerScope, User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


async def make_context_with_task(user: User) -> tuple[AppContext, str]:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    project = await projects.create(name="Launch", owner=OwnerScope(owner_id="alice"))
    task = await tasks.create(project_id=project.id, title="original")
    return AppContext(
        projects=projects,
        tasks=tasks,
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=user,
    ), task.id


async def test_update_task_changes_only_provided_fields() -> None:
    context, task_id = await make_context_with_task(ALICE)

    updated = await update_task(
        GetTaskParams(task_id=task_id),
        UpdateTask(status=TaskStatus.DOING),
        context,
    )

    assert updated.status == TaskStatus.DOING
    assert updated.title == "original"

    updated = await update_task(
        GetTaskParams(task_id=task_id), UpdateTask(title="renamed"), context
    )

    assert updated.title == "renamed"
    assert updated.status == TaskStatus.DOING


async def test_update_task_with_no_changes_returns_the_task() -> None:
    context, task_id = await make_context_with_task(ALICE)

    updated = await update_task(GetTaskParams(task_id=task_id), UpdateTask(), context)

    assert updated.title == "original"
    assert updated.status == TaskStatus.TODO


async def test_update_task_hides_other_owners_tasks() -> None:
    context, task_id = await make_context_with_task(BOB)

    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id=task_id), UpdateTask(title="stolen"), context
        )

    assert excinfo.value.definition == task_not_found


async def test_update_task_reports_missing_tasks() -> None:
    context, _ = await make_context_with_task(ALICE)

    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id="missing"), UpdateTask(title="x"), context
        )

    assert excinfo.value.definition == task_not_found
