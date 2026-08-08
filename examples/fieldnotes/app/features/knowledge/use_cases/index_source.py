# doctor: public — invoked by the trusted indexing worker, not an HTTP caller.

from tenchi.errors import AppError

from app.server.context import AppContext
from app.shared.errors import source_not_found

from ..schemas import IndexSource, Passage, StoredSource

_WORDS_PER_PASSAGE = 80


def _passages_for(source: StoredSource) -> tuple[Passage, ...]:
    words = source.content.split()
    chunks = (
        words[index : index + _WORDS_PER_PASSAGE]
        for index in range(0, len(words), _WORDS_PER_PASSAGE)
    )
    return tuple(
        Passage(
            id=f"{source.id}:{position}",
            source_id=source.id,
            title=source.title,
            text=" ".join(chunk),
            position=position,
            url=source.url,
        )
        for position, chunk in enumerate(chunks)
        if chunk
    )


async def index_source(request: IndexSource, context: AppContext) -> None:
    source = await context.sources.get_for_indexing(request.source_id)
    if source is None:
        raise AppError(source_not_found)
    await context.passages.replace_source(source, _passages_for(source))
    await context.sources.mark_indexed(source.id)
