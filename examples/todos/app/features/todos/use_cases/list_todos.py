from app.server.context import AppContext

from ..schemas import Todo


async def list_todos(context: AppContext) -> list[Todo]:
    return await context.todos.list()
