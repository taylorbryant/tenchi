from app.server.context import AppContext
from app.shared.users import require_user

from ..schemas import Project


async def list_projects(context: AppContext) -> list[Project]:
    user = require_user(context.user)
    return await context.projects.list_owned_by(user.id)
