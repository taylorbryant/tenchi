from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..schemas import Todo


async def list_todos(context: AppContext) -> list[Todo]:
    owner = require_owner_scope(context.user)
    return await context.todos.list(owner=owner)
