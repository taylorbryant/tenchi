"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uv run tenchi dev

The lifespan ensures the schema once at startup; each request gets its own
connection and transaction via the request-scoped context factory —
committed when the use case succeeds, rolled back when it raises. The
bearer hook attaches the authenticated user. Demo tokens: ``alice-token``
and ``bob-token``.
"""

from app.infra.static_token_directory import StaticTokenDirectory
from app.server.hooks import create_bearer_hook
from app.server.observability import observe_request, observe_use_case
from app.server.routes import routes
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan
from app.shared.users import User
from tenchi.server import create_app

DEMO_TOKENS = {
    "alice-token": User(id="alice", name="Alice"),
    "bob-token": User(id="bob", name="Bob"),
}


app = create_app(
    routes=routes,
    context_factory=create_context,
    lifespan=create_lifespan(DATABASE_PATH),
    hooks=[create_bearer_hook(StaticTokenDirectory(DEMO_TOKENS))],
    observers=[observe_request],
    use_case_observers=[observe_use_case],
)
