# doctor: public — invoked by the trusted worker, not an HTTP route; the
# authorization decision was made by add_project_member before enqueueing.
from app.server.context import AppContext

from ..schemas import MemberAdded


async def notify_member_added(request: MemberAdded, context: AppContext) -> None:
    """Deliver the notification announced by a ``member_added`` job."""
    project = await context.projects.get(request.project_id)
    project_name = project.name if project is not None else request.project_id
    await context.notifications.record(
        user_id=request.user_id,
        message=f"You were added to project {project_name!r}",
    )
