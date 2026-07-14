from app.server.context import AppContext
from app.shared.users import require_user

from ..schemas import CreateProject, Project


async def create_project(request: CreateProject, context: AppContext) -> Project:
    user = require_user(context.user)
    return await context.projects.create(name=request.name, owner_id=user.id)
