"""Offline answer adapter used by local development and deterministic CI."""

from collections.abc import Sequence

from app.features.knowledge.schemas import GeneratedAnswer, Passage, PassageMatch


class DeterministicAnswerGenerator:
    """Return the highest-ranked passage verbatim with an exact citation.

    A production adapter can call any model SDK behind the same port. Keeping
    this implementation deliberately plain makes the reference app useful
    without credentials and gives its evaluation gate a stable baseline.
    """

    async def generate(
        self,
        *,
        question: str,
        passages: Sequence[PassageMatch | Passage],
    ) -> GeneratedAnswer:
        if not passages:
            text = "I do not have enough indexed evidence to answer that question."
            return GeneratedAnswer(
                text=text,
                tokens=len(question.split()) + len(text.split()),
            )
        selected = passages[0]
        text = f"Based on {selected.title}: {selected.text}"
        return GeneratedAnswer(
            text=text,
            cited_passage_ids=(selected.id,),
            tokens=len(question.split()) + len(text.split()),
        )
