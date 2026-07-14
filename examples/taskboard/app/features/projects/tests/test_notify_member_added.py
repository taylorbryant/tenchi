from app.features.projects.schemas import MemberAdded
from app.features.projects.use_cases.notify_member_added import notify_member_added
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.server.context import AppContext


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

    await notify_member_added(
        MemberAdded(project_id="p1", project_name="Launch", user_id="bob"),
        context,
    )

    assert notifications.records == [("bob", "You were added to project 'Launch'")]


async def test_delivery_does_not_depend_on_current_project_state() -> None:
    # The payload is self-contained: the project was renamed (or deleted)
    # after enqueue, and the notification still reports the enqueue-time
    # facts without reading the repository.
    context, notifications = make_context()

    await notify_member_added(
        MemberAdded(project_id="gone", project_name="Old Name", user_id="bob"),
        context,
    )

    assert notifications.records == [("bob", "You were added to project 'Old Name'")]
