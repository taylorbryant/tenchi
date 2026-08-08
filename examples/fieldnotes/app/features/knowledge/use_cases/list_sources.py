from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..schemas import Source


async def list_sources(context: AppContext) -> list[Source]:
    owner = require_owner_scope(context.user)
    return await context.sources.list_owned_by(owner)
