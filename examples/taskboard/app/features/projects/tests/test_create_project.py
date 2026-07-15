import pytest

from app.features.projects.schemas import CreateProject
from app.features.projects.use_cases.create_project import create_project
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from app.shared.errors import unauthorized
from app.shared.users import User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")


def make_context(user: User | None = ALICE) -> AppContext:
    projects = MemoryProjectRepository()
    return AppContext(
        projects=projects,
        tasks=(tasks := MemoryTaskRepository(projects)),
        task_search=MemoryTaskSearch(projects, tasks),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=user,
    )


async def test_create_project_sets_owner_from_context_user() -> None:
    context = make_context()

    project = await create_project(CreateProject(name="Launch"), context)

    assert project.name == "Launch"
    assert project.owner_id == "alice"
    assert await context.projects.get(project.id) == project


async def test_create_project_requires_an_authenticated_user() -> None:
    context = make_context(user=None)

    with pytest.raises(AppError) as excinfo:
        await create_project(CreateProject(name="Launch"), context)

    assert excinfo.value.definition == unauthorized
