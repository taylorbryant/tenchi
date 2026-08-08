from tenchi.routes import route, route_group

from .contracts import (
    answer_question_contract,
    list_sources_contract,
    save_source_contract,
    search_knowledge_contract,
)
from .use_cases.answer_question import answer_question
from .use_cases.list_sources import list_sources
from .use_cases.save_source import save_source
from .use_cases.search_knowledge import search_knowledge

routes = route_group(
    route(save_source_contract, save_source),
    route(list_sources_contract, list_sources),
    route(search_knowledge_contract, search_knowledge),
    route(answer_question_contract, answer_question),
)
