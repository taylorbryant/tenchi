from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..schemas import SearchKnowledge, SearchResults


async def search_knowledge(
    request: SearchKnowledge,
    context: AppContext,
) -> SearchResults:
    owner = require_owner_scope(context.user)
    matches = await context.passages.search(
        request.query,
        owner=owner,
        limit=request.limit,
    )
    return SearchResults(matches=tuple(matches))
