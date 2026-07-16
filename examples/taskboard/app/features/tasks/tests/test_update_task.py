import pytest

from app.features.tasks.schemas import (
    GetTaskParams,
    TaskStatus,
    UpdateTask,
    UpdateTaskHeaders,
)
from app.features.tasks.use_cases.update_task import update_task
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from app.shared.errors import (
    forbidden,
    precondition_failed,
    precondition_required,
    task_not_found,
)
from app.shared.users import OwnerScope, User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


def matching(version: int = 1) -> UpdateTaskHeaders:
    return UpdateTaskHeaders(if_match=f'"{version}"')


async def make_context_with_task(user: User) -> tuple[AppContext, str]:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    project = await projects.create(name="Launch", owner=OwnerScope(owner_id="alice"))
    task = await tasks.create(project_id=project.id, title="original")
    return AppContext(
        projects=projects,
        tasks=tasks,
        task_search=MemoryTaskSearch(projects, tasks),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=user,
    ), task.id


async def test_update_task_changes_only_provided_fields() -> None:
    context, task_id = await make_context_with_task(ALICE)

    updated = await update_task(
        GetTaskParams(task_id=task_id),
        matching(),
        UpdateTask(status=TaskStatus.DOING),
        context,
    )

    assert updated.status == TaskStatus.DOING
    assert updated.title == "original"
    assert updated.version == 2

    updated = await update_task(
        GetTaskParams(task_id=task_id),
        matching(updated.version),
        UpdateTask(title="renamed"),
        context,
    )

    assert updated.title == "renamed"
    assert updated.status == TaskStatus.DOING
    assert updated.version == 3


async def test_update_task_with_no_changes_returns_the_task() -> None:
    context, task_id = await make_context_with_task(ALICE)

    updated = await update_task(
        GetTaskParams(task_id=task_id), matching(), UpdateTask(), context
    )

    assert updated.title == "original"
    assert updated.status == TaskStatus.TODO
    assert updated.version == 1


async def test_update_task_hides_other_owners_tasks() -> None:
    context, task_id = await make_context_with_task(BOB)

    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id=task_id),
            UpdateTaskHeaders(),
            UpdateTask(title="stolen"),
            context,
        )

    assert excinfo.value.definition == task_not_found


async def test_update_task_reports_missing_tasks() -> None:
    context, _ = await make_context_with_task(ALICE)

    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id="missing"),
            matching(),
            UpdateTask(title="x"),
            context,
        )

    assert excinfo.value.definition == task_not_found


async def test_members_may_view_but_not_update() -> None:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    project = await projects.create(name="Launch", owner=OwnerScope(owner_id="alice"))
    await projects.save(project.model_copy(update={"member_ids": ("bob",)}))
    task = await tasks.create(project_id=project.id, title="original")

    bob = AppContext(
        projects=projects,
        tasks=tasks,
        task_search=MemoryTaskSearch(projects, tasks),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=BOB,
    )

    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id=task.id),
            UpdateTaskHeaders(),
            UpdateTask(title="renamed"),
            bob,
        )

    # Visible (no 404 masking) but not writable: updating takes the same
    # ability as creating.
    assert excinfo.value.definition == forbidden


async def test_update_task_requires_if_match_after_authorization() -> None:
    context, task_id = await make_context_with_task(ALICE)

    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id=task_id),
            UpdateTaskHeaders(),
            UpdateTask(title="renamed"),
            context,
        )

    assert excinfo.value.definition == precondition_required


async def test_update_task_rejects_a_stale_version_without_overwriting() -> None:
    context, task_id = await make_context_with_task(ALICE)

    first = await update_task(
        GetTaskParams(task_id=task_id),
        matching(),
        UpdateTask(title="first writer"),
        context,
    )
    with pytest.raises(AppError) as excinfo:
        await update_task(
            GetTaskParams(task_id=task_id),
            matching(),
            UpdateTask(title="stale writer"),
            context,
        )

    assert excinfo.value.definition == precondition_failed
    stored = await context.tasks.get(task_id)
    assert stored == first
