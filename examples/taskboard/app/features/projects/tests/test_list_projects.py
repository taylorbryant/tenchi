from app.features.projects.use_cases.list_projects import list_projects
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.server.context import AppContext
from app.shared.users import OwnerScope, User

ALICE = User(id="alice", name="Alice")


async def test_list_projects_returns_only_the_current_users() -> None:
    projects = MemoryProjectRepository()
    context = AppContext(
        projects=projects,
        tasks=MemoryTaskRepository(projects),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
        user=ALICE,
    )
    mine = await projects.create(name="Mine", owner=OwnerScope(owner_id="alice"))
    await projects.create(name="Theirs", owner=OwnerScope(owner_id="bob"))

    assert await list_projects(context) == [mine]
