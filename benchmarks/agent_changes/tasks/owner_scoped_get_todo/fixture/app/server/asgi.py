"""Concrete authentication, persistence, and ASGI composition."""

from starlette.applications import Starlette
from tenchi.server import create_app

from app.infra.static_token_directory import StaticTokenDirectory
from app.server.hooks import create_bearer_hook
from app.server.routes import routes
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan
from app.shared.users import User

DEMO_TOKENS = {
    "alice-token": User(id="alice", name="Alice"),
    "bob-token": User(id="bob", name="Bob"),
}


def build_app(database_path: str = DATABASE_PATH) -> Starlette:
    return create_app(
        routes=routes,
        context_factory=create_context,
        lifespan=create_lifespan(database_path),
        hooks=(create_bearer_hook(StaticTokenDirectory(DEMO_TOKENS)),),
    )


app = build_app()
