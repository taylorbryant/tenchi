from app.features.todos.routes import routes as todo_routes
from app.shared.errors import unauthorized
from tenchi.openapi import openapi_route
from tenchi.routes import route_group

# Every API route may return the hook-raised UNAUTHORIZED error; declaring
# it at the group level keeps contracts honest and documents the 401.
api_routes = route_group(todo_routes, errors=(unauthorized,))

routes = route_group(
    api_routes,
    openapi_route(api_routes, title="Todos", version="0.1.0"),
)
