"""Concrete HTTP composition for the Fieldnotes reference backend."""

from collections.abc import Mapping

from starlette.applications import Starlette
from tenchi.server import create_app

from app.infra.static_token_directory import DEMO_TOKENS, StaticTokenDirectory
from app.server.hooks import create_bearer_hook
from app.server.observability import request_observers, use_case_observers
from app.server.routes import routes
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan
from app.shared.users import User


def create_fieldnotes_app(
    *,
    database_path: str,
    tokens: Mapping[str, User] = DEMO_TOKENS,
) -> Starlette:
    return create_app(
        routes=routes,
        context_factory=create_context,
        lifespan=create_lifespan(database_path),
        hooks=[create_bearer_hook(StaticTokenDirectory(tokens))],
        observers=request_observers,
        use_case_observers=use_case_observers,
    )


app = create_fieldnotes_app(database_path=DATABASE_PATH)
