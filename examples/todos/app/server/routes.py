from app.features.todos.routes import routes as todo_routes
from tenchi.routes import route_group

routes = route_group(todo_routes)
