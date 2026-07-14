from app.features.projects.schemas import MemberAdded
from app.features.projects.use_cases.notify_member_added import notify_member_added
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.server.context import AppContext
from app.shared.users import OwnerScope


def make_context() -> tuple[AppContext, MemoryNotificationLog]:
    projects = MemoryProjectRepository()
    notifications = MemoryNotificationLog()
    context = AppContext(
        projects=projects,
        tasks=MemoryTaskRepository(projects),
        outbox=MemoryOutbox(),
        notifications=notifications,
    )
    return context, notifications


async def test_notification_names_the_project() -> None:
    context, notifications = make_context()
    project = await context.projects.create(
        name="Launch", owner=OwnerScope(owner_id="alice")
    )

    await notify_member_added(
        MemberAdded(project_id=project.id, user_id="bob"), context
    )

    assert notifications.records == [("bob", "You were added to project 'Launch'")]


async def test_vanished_project_falls_back_to_its_id() -> None:
    context, notifications = make_context()

    await notify_member_added(MemberAdded(project_id="gone", user_id="bob"), context)

    assert notifications.records == [("bob", "You were added to project 'gone'")]
