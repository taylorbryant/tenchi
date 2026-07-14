from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..schemas import Project


async def list_projects(context: AppContext) -> list[Project]:
    owner = require_owner_scope(context.user)
    return await context.projects.list_owned_by(owner)
