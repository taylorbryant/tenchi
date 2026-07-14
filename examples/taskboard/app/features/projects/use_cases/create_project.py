from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..schemas import CreateProject, Project


async def create_project(request: CreateProject, context: AppContext) -> Project:
    owner = require_owner_scope(context.user)
    return await context.projects.create(name=request.name, owner=owner)
