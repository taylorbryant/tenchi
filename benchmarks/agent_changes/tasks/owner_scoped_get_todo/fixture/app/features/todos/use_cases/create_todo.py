from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..schemas import CreateTodo, Todo


async def create_todo(request: CreateTodo, context: AppContext) -> Todo:
    owner = require_owner_scope(context.user)
    return await context.todos.create(title=request.title, owner=owner)
