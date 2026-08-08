"""Deterministic retrieval, answer-evidence, and citation release gates."""

from typing import Protocol

from tenchi.evaluations import (
    EvaluationMeasurement,
    evaluation,
    evaluation_case,
    evaluation_group,
    evaluation_metric,
    evaluation_result,
)

from .policy import rank_texts
from .ports import AnswerGenerator
from .schemas import AnswerCase, Passage, RankingCandidate, RankingCase


class AnswerEvaluationContext(Protocol):
    answer_generator: AnswerGenerator


async def evaluate_ranking(
    case: RankingCase,
    context: object,
) -> EvaluationMeasurement:
    del context
    ranked = rank_texts(
        case.query,
        (
            (candidate.id, f"{candidate.title} {candidate.text}")
            for candidate in case.candidates
        ),
    )
    return evaluation_result(
        scores={
            "top_result": float(bool(ranked) and ranked[0].id == case.expected_first)
        }
    )


async def evaluate_answer(
    case: AnswerCase,
    context: AnswerEvaluationContext,
) -> EvaluationMeasurement:
    generated = await context.answer_generator.generate(
        question=case.question,
        passages=case.passages,
    )
    allowed = {passage.id for passage in case.passages}
    cited = set(generated.cited_passage_ids)
    return evaluation_result(
        scores={
            "evidence_retained": float(
                case.required_text.casefold() in generated.text.casefold()
            ),
            "citation_valid": float(bool(cited) and cited <= allowed),
        },
        tokens=generated.tokens,
        cost_usd=generated.cost_usd,
    )


retrieval_ranking = evaluation(
    "knowledge.retrieval_ranking",
    case=RankingCase,
    cases=(
        evaluation_case(
            "knowledge.retrieval_ranking.mcp",
            RankingCase(
                query="mcp authorization",
                candidates=(
                    RankingCandidate(
                        id="security",
                        title="MCP security",
                        text="Authorization and approval protect tool calls.",
                    ),
                    RankingCandidate(
                        id="cooking",
                        title="Dinner",
                        text="A recipe for tomato soup.",
                    ),
                ),
                expected_first="security",
            ),
        ),
    ),
    metrics=(
        evaluation_metric(
            "top_result",
            threshold=1.0,
            description="The expected evidence is ranked first.",
        ),
    ),
    evaluator=evaluate_ranking,
    description="Guard deterministic passage retrieval.",
)

answer_evidence = evaluation(
    "knowledge.answer_evidence",
    case=AnswerCase,
    cases=(
        evaluation_case(
            "knowledge.answer_evidence.citations",
            AnswerCase(
                question="What protects destructive MCP tools?",
                passages=(
                    Passage(
                        id="security:0",
                        source_id="security",
                        title="MCP security",
                        text="Explicit approval protects destructive MCP tools.",
                        position=0,
                    ),
                ),
                required_text="explicit approval",
            ),
        ),
    ),
    metrics=(
        evaluation_metric(
            "evidence_retained",
            threshold=1.0,
            description="The answer retains the required evidence.",
        ),
        evaluation_metric(
            "citation_valid",
            threshold=1.0,
            description="Every citation names supplied evidence.",
        ),
    ),
    evaluator=evaluate_answer,
    description="Guard required evidence and citation integrity.",
    max_tokens=1_000,
)

evaluations = evaluation_group(retrieval_ranking, answer_evidence)
