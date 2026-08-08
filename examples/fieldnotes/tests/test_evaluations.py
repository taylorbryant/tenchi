from pathlib import Path

import pytest
from app.features.knowledge.evaluations import evaluations
from app.features.knowledge.schemas import JSON_SAFE_INTEGER_MAX, GeneratedAnswer
from app.server.runtime import create_context, create_lifespan
from pydantic import ValidationError
from tenchi.evaluations import create_evaluation_runner


async def test_retrieval_and_answer_evaluations_pass(tmp_path: Path) -> None:
    database_path = str(tmp_path / "fieldnotes.db")
    runner = create_evaluation_runner(
        evaluations=evaluations,
        context_factory=create_context,
        lifespan=create_lifespan(database_path),
        concurrency=2,
    )

    report = await runner.run()

    assert report.ok is True
    assert [item.name for item in report.evaluations] == [
        "knowledge.retrieval_ranking",
        "knowledge.answer_evidence",
    ]
    assert all(
        metric.average == 1.0 for item in report.evaluations for metric in item.metrics
    )


def test_provider_token_usage_must_fit_json_safe_integer_range() -> None:
    with pytest.raises(ValidationError):
        GeneratedAnswer(
            text="answer",
            tokens=JSON_SAFE_INTEGER_MAX + 1,
        )
