# doctor: public — this is an operator-authorized task, not an HTTP route.

from tenchi.jobs import job_message

from app.server.context import AppContext

from ..jobs import index_source_job
from ..schemas import IndexSource, ReindexSourcesInput, ReindexSourcesResult


async def reindex_sources(
    request: ReindexSourcesInput,
    context: AppContext,
) -> ReindexSourcesResult:
    sources = await context.sources.list_all()
    if not request.dry_run:
        for source in sources:
            message = job_message(
                index_source_job,
                IndexSource(source_id=source.id),
            )
            await context.outbox.enqueue(
                job=message.name,
                payload_json=message.payload_json,
            )
    return ReindexSourcesResult(
        discovered=len(sources),
        enqueued=0 if request.dry_run else len(sources),
        dry_run=request.dry_run,
    )
