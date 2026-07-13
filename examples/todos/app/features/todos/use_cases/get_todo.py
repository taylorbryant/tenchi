from app.server.context import AppContext
from app.shared.errors import todo_not_found
from tenchi.errors import AppError

from ..schemas import GetTodoParams, Todo


async def get_todo(params: GetTodoParams, context: AppContext) -> Todo:
    todo = await context.todos.get(params.todo_id)
    if todo is None:
        raise AppError(todo_not_found, details={"todo_id": params.todo_id})
    return todo
