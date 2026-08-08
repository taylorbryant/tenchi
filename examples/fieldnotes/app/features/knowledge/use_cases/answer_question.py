import asyncio

from tenchi.errors import AppError

from app.server.context import AppContext
from app.shared.errors import answer_generation_failed, answer_generation_timed_out
from app.shared.users import require_owner_scope

from ..schemas import Answer, AskQuestion, Citation


async def answer_question(request: AskQuestion, context: AppContext) -> Answer:
    owner = require_owner_scope(context.user)
    passages = await context.passages.search(
        request.question,
        owner=owner,
        limit=request.limit,
    )
    try:
        async with asyncio.timeout(context.answer_timeout_seconds):
            generated = await context.answer_generator.generate(
                question=request.question,
                passages=tuple(passages),
            )
    except TimeoutError as error:
        raise AppError(answer_generation_timed_out) from error

    by_id = {passage.id: passage for passage in passages}
    cited_ids = generated.cited_passage_ids
    if len(cited_ids) != len(set(cited_ids)) or any(
        passage_id not in by_id for passage_id in cited_ids
    ):
        raise AppError(answer_generation_failed)

    citations = tuple(
        Citation(
            passage_id=passage.id,
            source_id=passage.source_id,
            title=passage.title,
            url=passage.url,
        )
        for passage_id in cited_ids
        for passage in (by_id[passage_id],)
    )
    return Answer(
        text=generated.text,
        citations=citations,
        has_citations=bool(citations),
        tokens=generated.tokens,
    )
