from tenchi.health import health_route
from tenchi.openapi import openapi_route, swagger_ui_route
from tenchi.routes import route_group

from app.features.todos.routes import routes as todo_routes
from app.shared.errors import unauthorized

OPENAPI_TITLE = "tenchi_benchmark"
OPENAPI_VERSION = "0.1.0"
OPENAPI_DESCRIPTION: str | None = None
OPENAPI_SECURITY = {"bearerAuth": {"type": "http", "scheme": "bearer"}}

api_routes = route_group(todo_routes, errors=(unauthorized,))

routes = route_group(
    api_routes,
    openapi_route(
        api_routes,
        title=OPENAPI_TITLE,
        version=OPENAPI_VERSION,
        description=OPENAPI_DESCRIPTION,
        security=OPENAPI_SECURITY,
    ),
    swagger_ui_route(title=f"{OPENAPI_TITLE} API"),
    health_route(),
)
