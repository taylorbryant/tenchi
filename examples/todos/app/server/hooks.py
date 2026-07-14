"""HTTP-boundary hooks.

Authentication lives here; business authorization belongs in use cases.
"""

import hmac
import os

from app.server.context import AppContext
from app.shared.errors import unauthorized
from tenchi.errors import AppError
from tenchi.server import RequestInfo


def require_api_key(info: RequestInfo, context: AppContext) -> None:
    """Reject requests that lack the configured API key.

    Disabled when ``TODOS_API_KEY`` is unset, so local quickstarts stay
    open. The OpenAPI document and health route stay public either way,
    exempted by their tags.
    """
    expected = os.environ.get("TODOS_API_KEY")
    if expected is None:
        return
    if {"docs", "health"} & set(info.contract.tags):
        return
    provided = info.headers.get("x-api-key", "")
    # Constant-time comparison: a plain != leaks key prefixes via timing.
    if not hmac.compare_digest(provided, expected):
        raise AppError(unauthorized)
