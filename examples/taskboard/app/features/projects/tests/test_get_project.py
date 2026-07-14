import pytest

from app.features.projects.schemas import GetProjectParams
from app.features.projects.use_cases.get_project import get_project
from app.infra.memory_repositories import (
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.server.context import AppContext
from app.shared.errors import project_not_found
from app.shared.users import User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


def make_context(user: User) -> AppContext:
    projects = MemoryProjectRepository()
    return AppContext(
        projects=projects, tasks=MemoryTaskRepository(projects), user=user
    )


async def test_get_project_returns_an_owned_project() -> None:
    context = make_context(ALICE)
    created = await context.projects.create(name="Launch", owner_id="alice")

    found = await get_project(GetProjectParams(project_id=created.id), context)

    assert found == created


async def test_get_project_hides_other_owners_projects() -> None:
    context = make_context(BOB)
    created = await context.projects.create(name="Launch", owner_id="alice")

    with pytest.raises(AppError) as excinfo:
        await get_project(GetProjectParams(project_id=created.id), context)

    assert excinfo.value.definition == project_not_found


async def test_get_project_reports_missing_projects() -> None:
    context = make_context(ALICE)

    with pytest.raises(AppError) as excinfo:
        await get_project(GetProjectParams(project_id="missing"), context)

    assert excinfo.value.definition == project_not_found
