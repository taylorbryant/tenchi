from app.server.context import AppContext

from ..schemas import ListTodosQuery, Todo


async def list_todos(query: ListTodosQuery, context: AppContext) -> list[Todo]:
    todos = await context.todos.list()
    if query.completed is not None:
        todos = [todo for todo in todos if todo.completed == query.completed]
    return todos
