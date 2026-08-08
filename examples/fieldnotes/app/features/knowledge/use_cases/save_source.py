from tenchi.jobs import job_message

from app.server.context import AppContext
from app.shared.users import require_owner_scope

from ..jobs import index_source_job
from ..schemas import IndexSource, SaveSource, Source


async def save_source(request: SaveSource, context: AppContext) -> Source:
    owner = require_owner_scope(context.user)
    source = await context.sources.create(
        title=request.title,
        content=request.content,
        url=str(request.url) if request.url is not None else None,
        owner=owner,
    )
    message = job_message(
        index_source_job,
        IndexSource(source_id=source.id),
    )
    await context.outbox.enqueue(job=message.name, payload_json=message.payload_json)
    return source
