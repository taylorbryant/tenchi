"""HTTP-boundary hooks.

Authentication lives here; business authorization (ownership rules)
belongs in use cases.
"""

from dataclasses import replace

from app.server.context import AppContext
from app.shared.errors import unauthorized
from app.shared.users import TokenDirectory
from tenchi.errors import AppError
from tenchi.server import Hook, RequestInfo


def create_bearer_hook(directory: TokenDirectory) -> Hook:
    """Authenticate ``Authorization: Bearer <token>`` against a directory.

    The OpenAPI document stays public; everything else requires a token
    the directory recognizes. Identity lands on ``context.user``.
    """

    async def authenticate(info: RequestInfo, context: AppContext) -> AppContext | None:
        # Exempt by tag, not by path: the docs and health routes carry
        # their tags wherever they are mounted.
        if {"docs", "health"} & set(info.contract.tags):
            return None
        scheme, _, token = info.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AppError(unauthorized)
        user = await directory.lookup(token)
        if user is None:
            raise AppError(unauthorized)
        return replace(context, user=user)

    return authenticate
