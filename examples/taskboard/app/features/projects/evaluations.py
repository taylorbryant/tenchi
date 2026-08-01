"""Deterministic baseline evaluations for project search ranking."""

from pydantic import BaseModel

from tenchi.evaluations import (
    EvaluationMeasurement,
    evaluation,
    evaluation_case,
    evaluation_group,
    evaluation_metric,
    evaluation_result,
)


class ProjectRankingCase(BaseModel, frozen=True):
    query: str
    candidates: tuple[str, ...]
    expected_first: str


def _rank_projects(case: ProjectRankingCase) -> tuple[str, ...]:
    query_terms = frozenset(case.query.casefold().split())
    return tuple(
        sorted(
            case.candidates,
            key=lambda title: (
                -len(query_terms.intersection(title.casefold().split())),
                title,
            ),
        )
    )


async def evaluate_project_ranking(
    case: ProjectRankingCase,
    context: object,
) -> EvaluationMeasurement:
    del context
    ranked = _rank_projects(case)
    return evaluation_result(
        scores={"top_result": float(bool(ranked) and ranked[0] == case.expected_first)}
    )


project_ranking = evaluation(
    "projects.search_ranking",
    case=ProjectRankingCase,
    cases=(
        evaluation_case(
            "projects.search_ranking.mobile",
            ProjectRankingCase(
                query="mobile launch",
                candidates=("API reliability", "Mobile launch", "Website refresh"),
                expected_first="Mobile launch",
            ),
        ),
        evaluation_case(
            "projects.search_ranking.api",
            ProjectRankingCase(
                query="api reliability",
                candidates=("API reliability", "Mobile launch", "Website refresh"),
                expected_first="API reliability",
            ),
        ),
    ),
    metrics=(
        evaluation_metric(
            "top_result",
            threshold=1.0,
            description="The expected project is ranked first.",
        ),
    ),
    evaluator=evaluate_project_ranking,
    description="Guard the deterministic ranking baseline used before model trials.",
)

evaluations = evaluation_group(project_ranking)
