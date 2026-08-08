from dataclasses import dataclass

from app.features.knowledge.ports import (
    AnswerGenerator,
    Outbox,
    PassageIndex,
    SourceRepository,
)
from app.shared.users import User


@dataclass(frozen=True, slots=True)
class AppContext:
    sources: SourceRepository
    passages: PassageIndex
    answer_generator: AnswerGenerator
    outbox: Outbox
    answer_timeout_seconds: float = 25.0
    user: User | None = None
