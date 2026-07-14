# doctor: public — invoked by the trusted worker, not an HTTP route; the
# authorization decision was made by add_project_member before enqueueing.
from app.server.context import AppContext

from ..schemas import MemberAdded


async def notify_member_added(request: MemberAdded, context: AppContext) -> None:
    """Deliver the notification announced by a ``member_added`` job.

    The payload is self-contained, so delivery does not depend on the
    project still existing or still having the same name.
    """
    await context.notifications.record(
        user_id=request.user_id,
        message=f"You were added to project {request.project_name!r}",
    )
