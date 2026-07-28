import pytest

from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from app.shared.errors import unauthorized
from tenchi.errors import AppError
from tenchi.idempotency import MemoryIdempotencyStore
from tenchi.rate_limits import MemoryRateLimitStore

from ..schemas import MemberAddedWebhook
from ..use_cases.receive_member_added_webhook import (
    MEMBER_DIRECTORY_SERVICE,
    receive_member_added_webhook,
)


def make_context(*, service: str | None) -> tuple[AppContext, MemoryNotificationLog]:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    notifications = MemoryNotificationLog()
    return (
        AppContext(
            projects=projects,
            tasks=tasks,
            task_search=MemoryTaskSearch(projects, tasks),
            idempotency=MemoryIdempotencyStore(),
            rate_limits=MemoryRateLimitStore(),
            outbox=MemoryOutbox(),
            notifications=notifications,
            service=service,
        ),
        notifications,
    )


async def test_webhook_requires_verified_service_identity() -> None:
    context, _ = make_context(service=None)
    request = MemberAddedWebhook(
        event_id="evt-1",
        project_id="project-1",
        project_name="Launch",
        user_id="bob",
    )

    with pytest.raises(AppError) as excinfo:
        await receive_member_added_webhook(request, context)

    assert excinfo.value.definition == unauthorized


async def test_webhook_delivery_is_idempotent_by_provider_event_id() -> None:
    context, notifications = make_context(service=MEMBER_DIRECTORY_SERVICE)
    request = MemberAddedWebhook(
        event_id="evt-1",
        project_id="project-1",
        project_name="Launch",
        user_id="bob",
    )

    await receive_member_added_webhook(request, context)
    await receive_member_added_webhook(request, context)

    assert notifications.records == [("bob", "You were added to project 'Launch'")]
