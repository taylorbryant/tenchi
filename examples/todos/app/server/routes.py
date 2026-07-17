from app.features.todos.routes import routes as todo_routes
from app.shared.errors import unauthorized
from tenchi.health import health_route
from tenchi.openapi import openapi_route
from tenchi.routes import route_group

OPENAPI_TITLE = "Todos"
OPENAPI_VERSION = "0.1.0"

# Every API route may return the hook-raised UNAUTHORIZED error; declaring
# it at the group level keeps contracts honest and documents the 401.
api_routes = route_group(todo_routes, errors=(unauthorized,))

routes = route_group(
    api_routes,
    openapi_route(api_routes, title=OPENAPI_TITLE, version=OPENAPI_VERSION),
    health_route(),
)
