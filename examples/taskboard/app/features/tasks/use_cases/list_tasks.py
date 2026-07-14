from app.server.context import AppContext
from app.shared.users import require_owner_scope
from tenchi.pagination import Page, page

from ..schemas import ListTasksQuery, Task


async def list_tasks(query: ListTasksQuery, context: AppContext) -> Page[Task]:
    owner = require_owner_scope(context.user)

    items, total = await context.tasks.search(
        owner=owner,
        project_id=query.project_id,
        status=query.status,
        limit=query.limit,
        offset=query.offset,
    )
    return page(items, total=total, query=query)
