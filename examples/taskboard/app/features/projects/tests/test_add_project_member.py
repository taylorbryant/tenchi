import pytest

from app.features.projects.schemas import AddProjectMember, GetProjectParams
from app.features.projects.use_cases.add_project_member import add_project_member
from app.features.projects.use_cases.get_project import get_project
from app.features.tasks.schemas import CreateTask
from app.features.tasks.use_cases.create_task import create_task
from app.infra.memory_repositories import (
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.server.context import AppContext
from app.shared.errors import forbidden
from app.shared.users import OwnerScope, User
from tenchi.errors import AppError

ALICE = User(id="alice", name="Alice")
BOB = User(id="bob", name="Bob")


def make_repositories() -> tuple[MemoryProjectRepository, MemoryTaskRepository]:
    projects = MemoryProjectRepository()
    return projects, MemoryTaskRepository(projects)


def context_for(
    user: User,
    projects: MemoryProjectRepository,
    tasks: MemoryTaskRepository,
) -> AppContext:
    return AppContext(projects=projects, tasks=tasks, user=user)


async def test_owner_adds_a_member_idempotently() -> None:
    projects, tasks = make_repositories()
    alice = context_for(ALICE, projects, tasks)
    project = await projects.create(name="Launch", owner=OwnerScope(owner_id="alice"))

    updated = await add_project_member(
        GetProjectParams(project_id=project.id),
        AddProjectMember(user_id="bob"),
        alice,
    )
    assert updated.member_ids == ("bob",)

    again = await add_project_member(
        GetProjectParams(project_id=project.id),
        AddProjectMember(user_id="bob"),
        alice,
    )
    assert again.member_ids == ("bob",)


async def test_non_owner_cannot_add_members() -> None:
    projects, tasks = make_repositories()
    bob = context_for(BOB, projects, tasks)
    project = await projects.create(name="Launch", owner=OwnerScope(owner_id="alice"))
    await projects.save(project.model_copy(update={"member_ids": ("bob",)}))

    # Even as a member, bob may view but not administer.
    with pytest.raises(AppError) as excinfo:
        await add_project_member(
            GetProjectParams(project_id=project.id),
            AddProjectMember(user_id="mallory"),
            bob,
        )

    assert excinfo.value.definition == forbidden


async def test_members_can_view_but_not_write() -> None:
    projects, tasks = make_repositories()
    alice = context_for(ALICE, projects, tasks)
    bob = context_for(BOB, projects, tasks)
    project = await projects.create(name="Launch", owner=OwnerScope(owner_id="alice"))

    await add_project_member(
        GetProjectParams(project_id=project.id),
        AddProjectMember(user_id="bob"),
        alice,
    )

    # Member view: fetch-then-ask grants bob read access.
    viewed = await get_project(GetProjectParams(project_id=project.id), bob)
    assert viewed.id == project.id

    # Member write: creating tasks stays owner-only.
    with pytest.raises(AppError) as excinfo:
        await create_task(CreateTask(project_id=project.id, title="nope"), bob)
    assert excinfo.value.definition == forbidden
