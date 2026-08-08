from app.features.knowledge.schemas import (
    AskQuestion,
    IndexSource,
    SaveSource,
    SearchKnowledge,
)
from app.features.knowledge.use_cases.answer_question import answer_question
from app.features.knowledge.use_cases.index_source import index_source
from app.features.knowledge.use_cases.save_source import save_source
from app.features.knowledge.use_cases.search_knowledge import search_knowledge
from app.infra.deterministic_answer_generator import DeterministicAnswerGenerator
from app.infra.memory_knowledge import (
    MemoryOutbox,
    MemoryPassageIndex,
    MemorySourceRepository,
)
from app.server.context import AppContext
from app.shared.users import User


async def test_save_index_search_and_answer_without_http() -> None:
    sources = MemorySourceRepository()
    passages = MemoryPassageIndex()
    outbox = MemoryOutbox()
    context = AppContext(
        sources=sources,
        passages=passages,
        answer_generator=DeterministicAnswerGenerator(),
        outbox=outbox,
        user=User(id="alice", name="Alice"),
    )

    source = await save_source(
        SaveSource(
            title="Grounded answers",
            content="Citations connect an answer to exact source evidence.",
        ),
        context,
    )
    assert outbox.entries[0].job == "knowledge.index_source"

    await index_source(IndexSource(source_id=source.id), context)

    search = await search_knowledge(SearchKnowledge(query="source evidence"), context)
    answer = await answer_question(
        AskQuestion(question="What connects an answer to evidence?"),
        context,
    )

    assert search.matches[0].source_id == source.id
    assert answer.has_citations is True
    assert answer.citations[0].source_id == source.id
