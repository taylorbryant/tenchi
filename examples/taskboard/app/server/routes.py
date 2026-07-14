from app.features.projects.routes import routes as project_routes
from app.features.tasks.routes import routes as task_routes
from app.shared.errors import unauthorized
from tenchi.openapi import openapi_route
from tenchi.routes import route_group

# Every API route may return the hook-raised UNAUTHORIZED error.
api_routes = route_group(
    project_routes,
    task_routes,
    errors=(unauthorized,),
)

routes = route_group(
    api_routes,
    openapi_route(api_routes, title="Taskboard", version="0.1.0"),
)
