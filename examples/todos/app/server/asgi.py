"""Server composition: concrete wiring and the ASGI application.

Run locally with:

    uvicorn app.server.asgi:app --reload

The lifespan ensures the SQLite schema at startup. Each request opens its own
connection and transaction through ``create_context`` so concurrent requests
cannot observe or commit each other's in-flight writes.
"""

from app.server.hooks import require_api_key
from app.server.routes import routes
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan
from tenchi.server import create_app

app = create_app(
    routes=routes,
    context_factory=create_context,
    lifespan=create_lifespan(DATABASE_PATH),
    hooks=[require_api_key],
)
