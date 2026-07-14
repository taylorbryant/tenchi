"""HTTP-boundary hooks.

Authentication lives here; business authorization belongs in use cases.
"""

import os

from app.server.context import AppContext
from app.shared.errors import unauthorized
from tenchi.errors import AppError
from tenchi.server import RequestInfo


def require_api_key(info: RequestInfo, context: AppContext) -> None:
    """Reject requests that lack the configured API key.

    Disabled when ``TODOS_API_KEY`` is unset, so local quickstarts stay
    open. The OpenAPI document stays public either way.
    """
    expected = os.environ.get("TODOS_API_KEY")
    if expected is None:
        return
    if info.contract.path == "/openapi.json" or "health" in info.contract.tags:
        return
    if info.headers.get("x-api-key") != expected:
        raise AppError(unauthorized)
