"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uvicorn app.server.app:app --reload
"""

from app.infra.port_wiring import create_todo_repository
from app.server.context import AppContext
from app.server.routes import routes
from tenchi.server import create_app

# Repositories are process-scoped; the context wrapping them is rebuilt for
# every request by ``create_context``.
todo_repository = create_todo_repository()


def create_context() -> AppContext:
    return AppContext(todos=todo_repository)


app = create_app(routes=routes, context_factory=create_context)
