from app.server.context import AppContext
from app.shared.users import require_service
from tenchi.idempotency import fingerprint, run_idempotently

from ..schemas import MemberAddedWebhook

MEMBER_DIRECTORY_SERVICE = "member-directory"


async def receive_member_added_webhook(
    request: MemberAddedWebhook,
    context: AppContext,
) -> None:
    """Record one notification from a verified member-directory delivery."""
    service = require_service(context.service, MEMBER_DIRECTORY_SERVICE)

    async def record_notification() -> None:
        await context.notifications.record(
            user_id=request.user_id,
            message=f"You were added to project {request.project_name!r}",
        )

    await run_idempotently(
        context.idempotency,
        namespace="webhooks.member_added",
        scope=service,
        key=request.event_id,
        fingerprint=fingerprint(request, annotation=MemberAddedWebhook),
        result_type=type(None),
        operation=record_notification,
        completed_ttl=7 * 24 * 60 * 60,
    )
