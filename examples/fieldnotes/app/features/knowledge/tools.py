from tenchi.tools import tool, tool_group, tool_handler

from app.shared.errors import (
    answer_generation_failed,
    answer_generation_timed_out,
    unauthorized,
)

from .schemas import (
    Answer,
    AskQuestion,
    SaveSource,
    SearchKnowledge,
    SearchResults,
    Source,
)
from .use_cases.answer_question import answer_question
from .use_cases.save_source import save_source
from .use_cases.search_knowledge import search_knowledge

search_knowledge_tool = tool(
    "knowledge.search",
    request=SearchKnowledge,
    result=SearchResults,
    description="Search passages visible to the authenticated user.",
    errors=(unauthorized,),
    read_only=True,
    open_world=False,
)

answer_question_tool = tool(
    "knowledge.answer",
    request=AskQuestion,
    result=Answer,
    description="Answer a question from visible indexed passages with citations.",
    errors=(unauthorized, answer_generation_failed, answer_generation_timed_out),
    read_only=True,
    open_world=True,
)

save_source_tool = tool(
    "sources.save",
    request=SaveSource,
    result=Source,
    description="Save material for background indexing.",
    errors=(unauthorized,),
    destructive=True,
    idempotent=False,
    open_world=False,
)

tools = tool_group(
    tool_handler(search_knowledge_tool, search_knowledge),
    tool_handler(answer_question_tool, answer_question),
    tool_handler(save_source_tool, save_source),
)
