from tenchi.health import health_route
from tenchi.openapi import openapi_route, swagger_ui_route
from tenchi.routes import route_group

from app.features.knowledge.routes import routes as knowledge_routes
from app.server.context import AppContext
from app.shared.errors import unauthorized
from app.shared.users import OwnerScope

OPENAPI_TITLE = "Fieldnotes"
OPENAPI_VERSION = "0.1.0"
OPENAPI_DESCRIPTION = (
    "A cited personal-research API built as Tenchi's AI reference backend."
)
OPENAPI_SECURITY = {"bearerAuth": {"type": "http", "scheme": "bearer"}}

api_routes = route_group(knowledge_routes, errors=(unauthorized,))


async def database_ready(context: AppContext) -> None:
    await context.sources.list_owned_by(OwnerScope(owner_id="__health__"))


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
    health_route(checks={"database": database_ready}),
)
