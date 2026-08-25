from app.server.context import AppContext

from ..schemas import CreateTodo, CreateTodoHeaders, Todo


async def create_todo(
    headers: CreateTodoHeaders,
    request: CreateTodo,
    context: AppContext,
) -> Todo:
    return await context.todos.create(title=request.title)
