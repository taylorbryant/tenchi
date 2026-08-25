from dataclasses import replace

from tenchi.errors import AppError
from tenchi.server import Hook, RequestInfo

from app.server.context import AppContext
from app.shared.errors import unauthorized
from app.shared.users import TokenDirectory


def create_bearer_hook(directory: TokenDirectory) -> Hook:
    async def authenticate(info: RequestInfo, context: AppContext) -> AppContext | None:
        if info.contract.public:
            return None
        scheme, _, token = info.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AppError(
                unauthorized,
                headers={"WWW-Authenticate": "Bearer"},
            )
        user = await directory.lookup(token)
        if user is None:
            raise AppError(
                unauthorized,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return replace(context, user=user)

    return authenticate
