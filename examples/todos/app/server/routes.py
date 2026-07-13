from app.features.todos.routes import routes as todo_routes
from tenchi.openapi import openapi_route
from tenchi.routes import route_group

api_routes = route_group(todo_routes)

routes = route_group(
    api_routes,
    openapi_route(api_routes, title="Todos", version="0.1.0"),
)
