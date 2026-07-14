from app.features.projects.routes import routes as project_routes
from app.features.tasks.routes import routes as task_routes
from app.server.context import AppContext
from app.shared.errors import unauthorized
from tenchi.health import health_route
from tenchi.openapi import openapi_route
from tenchi.routes import route_group

# Every API route may return the hook-raised UNAUTHORIZED error.
api_routes = route_group(
    project_routes,
    task_routes,
    errors=(unauthorized,),
)


async def database_ready(context: AppContext) -> None:
    """The shared database answers a trivial query."""
    await context.projects.list_owned_by("__health__")


routes = route_group(
    api_routes,
    openapi_route(api_routes, title="Taskboard", version="0.1.0"),
    health_route(checks={"database": database_ready}),
)
