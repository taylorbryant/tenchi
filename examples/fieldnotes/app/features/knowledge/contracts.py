from tenchi.contracts import contract

from app.shared.errors import answer_generation_failed, answer_generation_timed_out

from .schemas import (
    Answer,
    AskQuestion,
    SaveSource,
    SearchKnowledge,
    SearchResults,
    Source,
)

save_source_contract = contract(
    method="POST",
    path="/sources",
    request=SaveSource,
    response=Source,
    status=202,
    name="sources.save",
    summary="Save text or URL-backed material for background indexing",
    tags=("sources",),
    max_request_bytes=256 * 1024,
)

list_sources_contract = contract(
    method="GET",
    path="/sources",
    response=list[Source],
    name="sources.list",
    summary="List sources owned by the authenticated user",
    tags=("sources",),
)

search_knowledge_contract = contract(
    method="POST",
    path="/search",
    request=SearchKnowledge,
    response=SearchResults,
    name="knowledge.search",
    summary="Search indexed passages owned by the authenticated user",
    tags=("knowledge",),
)

answer_question_contract = contract(
    method="POST",
    path="/answers",
    request=AskQuestion,
    response=Answer,
    errors=(answer_generation_failed, answer_generation_timed_out),
    name="knowledge.answer",
    summary="Answer from indexed evidence and return exact citations",
    tags=("knowledge",),
    timeout=30,
)
