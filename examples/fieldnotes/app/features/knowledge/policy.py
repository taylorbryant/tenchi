"""Pure ranking policy for owner-visible knowledge passages."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_TERM = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class ScoredText:
    id: str
    score: float


def terms(value: str) -> frozenset[str]:
    return frozenset(_TERM.findall(value.casefold()))


def score_text(query: str, value: str) -> float:
    query_terms = terms(query)
    if not query_terms:
        return 0.0
    overlap = query_terms.intersection(terms(value))
    return len(overlap) / len(query_terms)


def rank_texts(
    query: str,
    values: Iterable[tuple[str, str]],
) -> tuple[ScoredText, ...]:
    scored = (
        ScoredText(id=item_id, score=score_text(query, value))
        for item_id, value in values
    )
    return tuple(
        sorted(
            (item for item in scored if item.score > 0),
            key=lambda item: (-item.score, item.id),
        )
    )
