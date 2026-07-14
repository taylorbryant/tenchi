from app.server.context import AppContext
from app.shared.users import require_user

from ..schemas import ListTasksQuery, TaskPage


async def list_tasks(query: ListTasksQuery, context: AppContext) -> TaskPage:
    user = require_user(context.user)

    items, total = await context.tasks.search(
        owner_id=user.id,
        project_id=query.project_id,
        status=query.status,
        limit=query.limit,
        offset=query.offset,
    )
    return TaskPage(items=items, total=total, limit=query.limit, offset=query.offset)
