"""Provider-neutral application evaluations fail closed and redact payloads."""

# pyright: strict, reportUnnecessaryTypeIgnoreComment=true

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, Field, TypeAdapter, field_validator

from tenchi._schema_compatibility import ChangeSeverity, compare_schema
from tenchi.errors import ConfigurationError
from tenchi.evaluations import (
    EVALUATION_MANIFEST_VERSION,
    MAX_EVALUATION_TOKENS,
    Evaluation,
    EvaluationBindingError,
    EvaluationCase,
    EvaluationGroup,
    EvaluationManifest,
    EvaluationMeasurement,
    EvaluationNotFoundError,
    EvaluationResultError,
    EvaluationRunner,
    create_evaluation_runner,
    evaluation,
    evaluation_case,
    evaluation_group,
    evaluation_manifest,
    evaluation_metric,
    evaluation_result,
)

EVALUATION_MANIFEST_SNAPSHOT_PATH = Path(__file__).with_name(
    f"evaluation_manifest_protocol_v{EVALUATION_MANIFEST_VERSION}_snapshot.json"
)
UPDATE_EVALUATION_MANIFEST_SNAPSHOT = "TENCHI_UPDATE_EVALUATION_MANIFEST_SNAPSHOT"
EVALUATION_MANIFEST_BASE_REF = "TENCHI_AGENT_PROTOCOL_BASE_REF"
REPOSITORY_ROOT = Path(__file__).parent.parent


class AnswerCase(BaseModel):
    prompt: str = Field(min_length=1)
    expected: str


@dataclass(frozen=True, slots=True)
class EvalContext:
    prefix: str


async def exact_match(
    case: AnswerCase,
    context: EvalContext,
) -> EvaluationMeasurement:
    return evaluation_result(
        scores={
            "quality": float(f"{context.prefix}{case.prompt}" == case.expected),
        },
        tokens=4,
        cost_usd=0.01,
    )


def answer_evaluation(
    *cases: EvaluationCase[AnswerCase],
    evaluator: Callable[
        [AnswerCase, EvalContext], Awaitable[EvaluationMeasurement]
    ] = exact_match,
    timeout: float = 1.0,
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
) -> Evaluation[AnswerCase, EvalContext]:
    return evaluation(
        "answers.quality",
        case=AnswerCase,
        cases=cases
        or (
            evaluation_case(
                "simple",
                AnswerCase(prompt="hello", expected="prefix:hello"),
            ),
        ),
        metrics=(
            evaluation_metric(
                "quality",
                threshold=0.8,
                description="  Exact answer quality.  ",
            ),
        ),
        evaluator=evaluator,
        kind="model",
        description="  Judge generated answers.  ",
        timeout=timeout,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
    )


@asynccontextmanager
async def context() -> AsyncGenerator[EvalContext]:
    yield EvalContext(prefix="prefix:")


def test_evaluation_declaration_validates_cases_and_public_metadata() -> None:
    declared = answer_evaluation(
        evaluation_case(
            "coerced",
            cast(AnswerCase, {"prompt": "hello", "expected": "prefix:hello"}),
        ),
        max_tokens=100,
        max_cost_usd=1,
    )

    assert declared.name == "answers.quality"
    assert declared.kind == "model"
    assert declared.description == "Judge generated answers."
    assert declared.timeout == 1.0
    assert declared.max_tokens == 100
    assert declared.max_cost_usd == 1.0
    assert declared.metrics[0].description == "Exact answer quality."
    assert isinstance(declared.cases[0].input, AnswerCase)
    assert declared._case_schema["type"] == "object"  # pyright: ignore[reportPrivateUsage]
    assert "hello" not in repr(declared)


def test_evaluation_manifest_is_canonical_payload_free_and_ordered() -> None:
    declared = answer_evaluation(
        evaluation_case(
            "answers.quality.zulu",
            AnswerCase(prompt="secret-zulu", expected="prefix:secret-zulu"),
        ),
        evaluation_case(
            "answers.quality.alpha",
            AnswerCase(prompt="secret-alpha", expected="prefix:secret-alpha"),
        ),
        max_tokens=100,
        max_cost_usd=1,
    )

    manifest = evaluation_manifest(evaluation_group(declared))
    serialized = json.dumps(manifest, sort_keys=True)

    assert manifest["schema_version"] == EVALUATION_MANIFEST_VERSION
    assert manifest["evaluations"][0]["cases"] == [
        "answers.quality.zulu",
        "answers.quality.alpha",
    ]
    assert manifest["evaluations"][0]["metrics"][0]["threshold"] == 0.8
    assert manifest["evaluations"][0]["max_tokens"] == 100
    assert "secret-alpha" not in serialized
    assert "secret-zulu" not in serialized


def test_evaluation_manifest_schema_advertises_runtime_constraints() -> None:
    schema = TypeAdapter(EvaluationManifest).json_schema(mode="serialization")
    entry = cast(dict[str, object], schema["$defs"]["EvaluationManifestEntry"])
    entry_properties = cast(dict[str, object], entry["properties"])
    metric = cast(dict[str, object], schema["$defs"]["EvaluationMetricManifest"])
    metric_properties = cast(dict[str, object], metric["properties"])

    assert entry_properties["name"] == {
        "pattern": r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$",
        "title": "Name",
        "type": "string",
    }
    assert cast(dict[str, object], entry_properties["cases"])["minItems"] == 1
    assert cast(dict[str, object], entry_properties["metrics"])["minItems"] == 1
    assert (
        cast(dict[str, object], entry_properties["timeout_seconds"])["exclusiveMinimum"]
        == 0
    )
    max_tokens = cast(dict[str, object], entry_properties["max_tokens"])
    token_schema = cast(list[dict[str, object]], max_tokens["anyOf"])[0]
    assert token_schema["minimum"] == 1
    assert token_schema["maximum"] == MAX_EVALUATION_TOKENS
    assert cast(dict[str, object], metric_properties["threshold"])["minimum"] == 0
    assert cast(dict[str, object], metric_properties["threshold"])["maximum"] == 1


def test_case_schema_returns_an_independent_copy() -> None:
    declared = answer_evaluation()
    schema = declared.case_schema
    properties = cast(dict[str, object], schema["properties"])
    prompt = cast(dict[str, object], properties["prompt"])

    prompt["description"] = "changed"

    current_properties = cast(dict[str, object], declared.case_schema["properties"])
    current_prompt = cast(dict[str, object], current_properties["prompt"])
    assert "description" not in current_prompt


def test_case_schema_rejects_nonportable_json_metadata() -> None:
    class HugeMetadataCase(BaseModel):
        value: int = Field(json_schema_extra={"example": 10**5000})

    async def score_huge(
        case: HugeMetadataCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"quality": 1})

    with pytest.raises(EvaluationBindingError, match="Pydantic cannot use case type"):
        evaluation(
            "answers.huge_metadata",
            case=HugeMetadataCase,
            cases=(evaluation_case("simple", HugeMetadataCase(value=1)),),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=score_huge,
        )

    class NonfiniteMetadataCase(BaseModel):
        value: int = Field(json_schema_extra={"example": float("nan")})

    async def score_nonfinite(
        case: NonfiniteMetadataCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"quality": 1})

    with pytest.raises(EvaluationBindingError, match="Pydantic cannot use case type"):
        evaluation(
            "answers.nonfinite_metadata",
            case=NonfiniteMetadataCase,
            cases=(evaluation_case("simple", NonfiniteMetadataCase(value=1)),),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=score_nonfinite,
        )


async def test_each_evaluator_invocation_receives_an_isolated_case_input() -> None:
    class MutableCase(BaseModel):
        values: list[int]

    async def mutate(
        case: MutableCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del context
        case.values.append(1)
        await asyncio.sleep(0)
        return evaluation_result(
            scores={"quality": float(case.values == [1])},
        )

    shared = MutableCase(values=[])
    declared = evaluation(
        "answers.mutable",
        case=MutableCase,
        cases=(
            evaluation_case("first", shared),
            evaluation_case("second", shared),
        ),
        metrics=(evaluation_metric("quality", threshold=1),),
        evaluator=mutate,
    )
    runner = create_evaluation_runner(
        evaluations=evaluation_group(declared),
        context_factory=context,
        concurrency=2,
    )

    first = await runner.run()
    second = await runner.run()

    for report in (first, second):
        assert report.ok
        assert [
            dict(item.scores)["quality"] for item in report.evaluations[0].cases
        ] == [1.0, 1.0]
    assert shared.values == []
    assert all(item.input.values == [] for item in declared.cases)
    assert declared.cases[0].input is not declared.cases[1].input


def test_existing_model_instances_are_revalidated_at_composition() -> None:
    case = AnswerCase(prompt="hello", expected="prefix:hello")
    object.__setattr__(case, "prompt", 42)

    with pytest.raises(EvaluationBindingError, match="does not match"):
        answer_evaluation(evaluation_case("invalid", case))


async def test_stored_model_instances_are_revalidated_before_invocation() -> None:
    invoked = False

    async def score(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        nonlocal invoked
        invoked = True
        del case, context
        return evaluation_result(scores={"quality": 1})

    declared = answer_evaluation(evaluator=score)
    object.__setattr__(declared.cases[0].input, "prompt", 42)
    runner = create_evaluation_runner(
        evaluations=evaluation_group(declared),
        context_factory=context,
    )

    report = await runner.run()

    assert not report.ok
    assert not invoked
    assert report.evaluations[0].cases[0].failure_code == "EVALUATION_RESULT_INVALID"


@pytest.mark.parametrize(
    "name",
    ["Bad", "bad-name", ".bad", "bad.", "bad..name"],
)
def test_evaluation_names_use_dotted_snake_case(name: str) -> None:
    with pytest.raises(EvaluationBindingError, match="dotted snake_case"):
        evaluation(
            name,
            case=AnswerCase,
            cases=(evaluation_case("simple", AnswerCase(prompt="x", expected="x")),),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=exact_match,
        )


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), True])
def test_evaluation_metrics_are_normalized(threshold: object) -> None:
    with pytest.raises(EvaluationBindingError, match="from 0 to 1"):
        evaluation_metric(
            "quality",
            threshold=threshold,  # pyright: ignore[reportArgumentType]
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("timeout", 0),
        ("timeout", float("inf")),
        ("timeout", 10**10_000),
        ("max_tokens", 0),
        ("max_tokens", True),
        ("max_tokens", MAX_EVALUATION_TOKENS + 1),
        ("max_cost_usd", 0),
        ("max_cost_usd", float("nan")),
    ],
    ids=[
        "zero-timeout",
        "infinite-timeout",
        "oversized-timeout",
        "zero-token-budget",
        "boolean-token-budget",
        "oversized-token-budget",
        "zero-cost-budget",
        "nan-cost-budget",
    ],
)
def test_evaluation_bounds_are_positive_and_finite(
    field_name: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {field_name: value}
    with pytest.raises(EvaluationBindingError):
        evaluation(
            "answers.quality",
            case=AnswerCase,
            cases=(evaluation_case("simple", AnswerCase(prompt="x", expected="x")),),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=exact_match,
            **kwargs,  # pyright: ignore[reportArgumentType]
        )


def test_evaluation_result_validates_scores_and_usage() -> None:
    result = evaluation_result(
        scores={"quality": 0.75},
        tokens=0,
        cost_usd=0,
    )

    assert result.scores == (("quality", 0.75),)
    assert result.tokens == 0
    assert result.cost_usd == 0.0

    with pytest.raises(EvaluationResultError, match="at least one score"):
        evaluation_result(scores={})
    with pytest.raises(EvaluationResultError, match="from 0 to 1"):
        evaluation_result(scores={"quality": float("inf")})
    with pytest.raises(EvaluationResultError, match="non-negative integer"):
        evaluation_result(scores={"quality": 1}, tokens=-1)
    with pytest.raises(EvaluationResultError, match="no greater than"):
        evaluation_result(
            scores={"quality": 1},
            tokens=MAX_EVALUATION_TOKENS + 1,
        )
    assert (
        evaluation_result(
            scores={"quality": 1},
            tokens=MAX_EVALUATION_TOKENS,
        ).tokens
        == MAX_EVALUATION_TOKENS
    )
    with pytest.raises(EvaluationResultError, match="name/value pair"):
        EvaluationMeasurement(
            scores=cast(tuple[tuple[str, float], ...], (("quality",),)),
        )


def test_case_validation_errors_are_normalized_at_composition() -> None:
    class CustomCase(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value: str) -> str:
            del value
            raise TypeError("secret validator detail")

    async def score(
        case: CustomCase,
        context: object,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"quality": 1})

    with pytest.raises(EvaluationBindingError, match="does not match"):
        evaluation(
            "answers.custom",
            case=CustomCase,
            cases=(
                evaluation_case(
                    "answers.custom.simple",
                    cast(CustomCase, {"value": "private"}),
                ),
            ),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=score,
        )


def test_evaluator_types_are_checked_statically_and_at_composition() -> None:
    async def wrong_case(
        case: str,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"quality": 1})

    async def wrong_result(case: AnswerCase, context: EvalContext) -> str:
        del case, context
        return "unsafe"

    with pytest.raises(EvaluationBindingError, match="does not match"):
        evaluation(
            "answers.case",
            case=AnswerCase,
            cases=(evaluation_case("simple", AnswerCase(prompt="x", expected="x")),),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=wrong_case,  # pyright: ignore[reportArgumentType]
        )
    with pytest.raises(EvaluationBindingError, match="must be EvaluationMeasurement"):
        evaluation(
            "answers.result",
            case=AnswerCase,
            cases=(evaluation_case("simple", AnswerCase(prompt="x", expected="x")),),
            metrics=(evaluation_metric("quality", threshold=1),),
            evaluator=wrong_result,  # pyright: ignore[reportArgumentType]
        )


def test_keyword_only_evaluators_are_rejected_at_composition() -> None:
    async def keyword_only(
        *,
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        return evaluation_result(
            scores={"quality": float(context.prefix + case.prompt == case.expected)}
        )

    with pytest.raises(EvaluationBindingError, match="positional argument"):
        answer_evaluation(
            evaluator=keyword_only,  # pyright: ignore[reportArgumentType]
        )


def test_direct_construction_cannot_bypass_group_and_runner_validation() -> None:
    declared = answer_evaluation()

    with pytest.raises(ConfigurationError, match="duplicate evaluation name"):
        EvaluationGroup(evaluations=(declared, declared))
    with pytest.raises(ConfigurationError, match="must be an Evaluation"):
        EvaluationGroup(
            evaluations=(cast(Evaluation[object, object], object()),),
        )
    with pytest.raises(ConfigurationError, match="must be an EvaluationGroup"):
        EvaluationRunner(
            evaluations=cast(EvaluationGroup, object()),
            context_factory=context,
        )
    with pytest.raises(ConfigurationError, match="must be a tuple"):
        EvaluationGroup(
            evaluations=cast(tuple[Evaluation[object, object], ...], [declared]),
        )


def test_evaluation_groups_flatten_and_reject_duplicate_names() -> None:
    first = answer_evaluation()

    async def other(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"quality": 1})

    second = evaluation(
        "answers.other",
        case=AnswerCase,
        cases=(evaluation_case("simple", AnswerCase(prompt="x", expected="x")),),
        metrics=(evaluation_metric("quality", threshold=1),),
        evaluator=other,
    )

    assert evaluation_group(first, evaluation_group(second)).evaluations == (
        first,
        second,
    )
    with pytest.raises(ConfigurationError, match="duplicate evaluation name"):
        evaluation_group(first, first)


async def test_runner_uses_one_lifespan_and_a_scoped_context_per_case() -> None:
    opened = 0
    closed = 0
    context_opened = 0
    context_closed = 0

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        nonlocal opened, closed
        opened += 1
        try:
            yield "prefix:"
        finally:
            closed += 1

    @asynccontextmanager
    async def scoped(state: str) -> AsyncGenerator[EvalContext]:
        nonlocal context_opened, context_closed
        context_opened += 1
        try:
            yield EvalContext(prefix=state)
        finally:
            context_closed += 1

    declared = answer_evaluation(
        evaluation_case(
            "first",
            AnswerCase(prompt="one", expected="prefix:one"),
        ),
        evaluation_case(
            "second",
            AnswerCase(prompt="two", expected="prefix:two"),
        ),
    )
    runner = create_evaluation_runner(
        evaluations=evaluation_group(declared),
        context_factory=scoped,
        lifespan=lifespan,
        concurrency=2,
    )

    report = await runner.run()

    assert report.ok
    assert [case.name for case in report.evaluations[0].cases] == [
        "first",
        "second",
    ]
    assert opened == closed == 1
    assert context_opened == context_closed == 2


async def test_runner_bounds_concurrency_and_preserves_case_order() -> None:
    active = 0
    maximum = 0
    release = asyncio.Event()
    started = asyncio.Event()

    async def wait_for_release(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        nonlocal active, maximum
        del case, context
        active += 1
        maximum = max(maximum, active)
        if maximum == 2:
            started.set()
        try:
            await release.wait()
            return evaluation_result(scores={"quality": 1})
        finally:
            active -= 1

    declared = answer_evaluation(
        *(
            evaluation_case(
                f"case_{index}",
                AnswerCase(prompt=str(index), expected=str(index)),
            )
            for index in range(4)
        ),
        evaluator=wait_for_release,
    )
    runner = create_evaluation_runner(
        evaluations=evaluation_group(declared),
        context_factory=context,
        concurrency=2,
    )

    pending = asyncio.create_task(runner.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    assert maximum == 2
    release.set()
    report = await pending

    assert [case.name for case in report.evaluations[0].cases] == [
        "case_0",
        "case_1",
        "case_2",
        "case_3",
    ]


async def test_threshold_and_usage_budgets_fail_closed_and_stop_new_batches() -> None:
    invoked: list[str] = []

    async def expensive(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del context
        invoked.append(case.prompt)
        return evaluation_result(
            scores={"quality": 0.5},
            tokens=6,
            cost_usd=0.6,
        )

    declared = answer_evaluation(
        *(
            evaluation_case(
                f"case_{index}",
                AnswerCase(prompt=str(index), expected=str(index)),
            )
            for index in range(3)
        ),
        evaluator=expensive,
        max_tokens=10,
        max_cost_usd=1,
    )
    runner = create_evaluation_runner(
        evaluations=evaluation_group(declared),
        context_factory=context,
        concurrency=2,
    )

    report = await runner.run()
    outcome = report.evaluations[0]

    assert not report.ok
    assert invoked == ["0", "1"]
    assert [case.status for case in outcome.cases] == [
        "completed",
        "completed",
        "skipped",
    ]
    assert outcome.cases[-1].failure_code == "EVALUATION_BUDGET_EXCEEDED"
    assert outcome.metrics[0].average == 0.5
    assert outcome.metrics[0].passed is False
    assert outcome.budget.consumed_tokens == 12
    assert outcome.budget.consumed_cost_usd == pytest.approx(1.2)
    assert outcome.budget.status == "exceeded"
    assert outcome.budget.passed is False


async def test_cost_budget_uses_exact_decimal_accounting() -> None:
    async def fractional_cost(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del context
        return evaluation_result(
            scores={"quality": 1},
            cost_usd={"one": 0.1, "two": 0.2}[case.prompt],
        )

    runner = create_evaluation_runner(
        evaluations=evaluation_group(
            answer_evaluation(
                evaluation_case(
                    "first",
                    AnswerCase(prompt="one", expected="prefix:one"),
                ),
                evaluation_case(
                    "second",
                    AnswerCase(prompt="two", expected="prefix:two"),
                ),
                evaluator=fractional_cost,
                max_cost_usd=0.3,
            )
        ),
        context_factory=context,
        concurrency=2,
    )

    report = await runner.run()

    assert report.ok
    assert report.evaluations[0].budget.consumed_cost_usd == 0.3
    assert report.evaluations[0].budget.status == "passed"


async def test_cost_budget_totals_that_overflow_fail_closed() -> None:
    async def huge_cost(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(
            scores={"quality": 1},
            cost_usd=1e308,
        )

    runner = create_evaluation_runner(
        evaluations=evaluation_group(
            answer_evaluation(
                evaluation_case(
                    "first",
                    AnswerCase(prompt="one", expected="prefix:one"),
                ),
                evaluation_case(
                    "second",
                    AnswerCase(prompt="two", expected="prefix:two"),
                ),
                evaluator=huge_cost,
                max_cost_usd=1e308,
            )
        ),
        context_factory=context,
        concurrency=2,
    )

    report = await runner.run()

    assert not report.ok
    assert report.evaluations[0].budget.consumed_cost_usd is None
    assert report.evaluations[0].budget.status == "exceeded"
    assert report.evaluations[0].budget.passed is False


async def test_declared_budget_requires_usage_in_every_measurement() -> None:
    async def missing_usage(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"quality": 1})

    runner = create_evaluation_runner(
        evaluations=evaluation_group(
            answer_evaluation(
                evaluation_case(
                    "first",
                    AnswerCase(prompt="one", expected="prefix:one"),
                ),
                evaluation_case(
                    "second",
                    AnswerCase(prompt="two", expected="prefix:two"),
                ),
                evaluator=missing_usage,
                max_tokens=10,
            )
        ),
        context_factory=context,
    )

    report = await runner.run()

    assert not report.ok
    assert report.evaluations[0].cases[0].failure_code == ("EVALUATION_RESULT_INVALID")
    assert report.evaluations[0].cases[1].status == "skipped"
    assert report.evaluations[0].cases[1].failure_code == (
        "EVALUATION_BUDGET_UNVERIFIED"
    )
    assert report.evaluations[0].budget.consumed_tokens is None
    assert report.evaluations[0].budget.status == "unverified"
    assert report.evaluations[0].budget.passed is False


async def test_case_failures_and_invalid_results_are_redacted() -> None:
    secret = "private model output"

    async def raises(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        raise RuntimeError(secret)

    async def missing_metric(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        return evaluation_result(scores={"other": 1})

    first = answer_evaluation(evaluator=raises)
    second = evaluation(
        "answers.invalid",
        case=AnswerCase,
        cases=(evaluation_case("simple", AnswerCase(prompt="x", expected="x")),),
        metrics=(evaluation_metric("quality", threshold=1),),
        evaluator=missing_metric,
    )
    runner = create_evaluation_runner(
        evaluations=evaluation_group(first, second),
        context_factory=context,
    )

    report = await runner.run()

    assert not report.ok
    assert report.evaluations[0].cases[0].failure_code == "EVALUATION_CASE_FAILED"
    assert report.evaluations[1].cases[0].failure_code == ("EVALUATION_RESULT_INVALID")
    assert secret not in repr(report)


async def test_case_timeout_cancels_cleanup_and_cannot_be_swallowed() -> None:
    cleaned = asyncio.Event()

    async def swallows_timeout(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        try:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        except asyncio.CancelledError:
            return evaluation_result(scores={"quality": 1})
        finally:
            cleaned.set()

    runner = create_evaluation_runner(
        evaluations=evaluation_group(
            answer_evaluation(
                evaluator=swallows_timeout,
                timeout=0.01,
            )
        ),
        context_factory=context,
    )

    report = await runner.run()

    assert report.evaluations[0].cases[0].status == "timed_out"
    assert cleaned.is_set()


async def test_dependency_timeout_is_a_failure_not_the_runner_deadline() -> None:
    async def dependency_timeout(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        raise TimeoutError("provider timed out")

    runner = create_evaluation_runner(
        evaluations=evaluation_group(answer_evaluation(evaluator=dependency_timeout)),
        context_factory=context,
    )

    report = await runner.run()

    assert report.evaluations[0].cases[0].status == "failed"
    assert report.evaluations[0].cases[0].failure_code == "EVALUATION_CASE_FAILED"


async def test_runner_cancellation_propagates_through_context_and_lifespan() -> None:
    started = asyncio.Event()
    context_closed = asyncio.Event()
    lifespan_closed = asyncio.Event()

    async def waits(
        case: AnswerCase,
        context: EvalContext,
    ) -> EvaluationMeasurement:
        del case, context
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[None]:
        try:
            yield None
        finally:
            lifespan_closed.set()

    @asynccontextmanager
    async def scoped() -> AsyncGenerator[EvalContext]:
        try:
            yield EvalContext(prefix="")
        finally:
            context_closed.set()

    runner = create_evaluation_runner(
        evaluations=evaluation_group(answer_evaluation(evaluator=waits)),
        context_factory=scoped,
        lifespan=lifespan,
    )
    pending = asyncio.create_task(runner.run())
    await started.wait()

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert context_closed.is_set()
    assert lifespan_closed.is_set()


async def test_runner_selects_one_evaluation_and_rejects_unknown_names() -> None:
    runner = create_evaluation_runner(
        evaluations=evaluation_group(answer_evaluation()),
        context_factory=context,
    )

    report = await runner.run("answers.quality")
    assert [item.name for item in report.evaluations] == ["answers.quality"]

    with pytest.raises(EvaluationNotFoundError, match="unknown evaluation"):
        await runner.run("missing")


async def test_empty_evaluation_group_is_a_successful_explicit_noop() -> None:
    runner = create_evaluation_runner(
        evaluations=evaluation_group(),
        context_factory=context,
    )

    report = await runner.run()

    assert report.ok
    assert report.evaluations == ()


@dataclass(frozen=True, slots=True)
class _ManifestProtocolChange:
    severity: ChangeSeverity
    location: str
    message: str


class _ManifestChangeSink:
    def __init__(self) -> None:
        self.changes: list[_ManifestProtocolChange] = []

    def add(self, severity: ChangeSeverity, location: str, message: str) -> None:
        self.changes.append(_ManifestProtocolChange(severity, location, message))


def _render_evaluation_manifest_protocol() -> dict[str, object]:
    adapter = TypeAdapter(EvaluationManifest)
    adapter.validate_python(
        {
            "schema_version": EVALUATION_MANIFEST_VERSION,
            "evaluations": [],
        },
        strict=True,
    )
    return {
        "schema_version": EVALUATION_MANIFEST_VERSION,
        "manifest_schema": adapter.json_schema(mode="serialization"),
    }


def _manifest_object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    assert all(isinstance(key, str) for key in mapping)
    return cast(dict[str, object], mapping)


def _load_evaluation_manifest_protocol() -> dict[str, object]:
    return _manifest_object(
        json.loads(EVALUATION_MANIFEST_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    )


def _evaluation_manifest_incompatibilities(
    baseline: dict[str, object],
    current: dict[str, object],
) -> list[_ManifestProtocolChange]:
    baseline_schema = _manifest_object(baseline["manifest_schema"])
    current_schema = _manifest_object(current["manifest_schema"])
    sink = _ManifestChangeSink()
    compare_schema(
        {key: value for key, value in baseline_schema.items() if key != "$defs"},
        {key: value for key, value in current_schema.items() if key != "$defs"},
        direction="output",
        baseline=baseline_schema,
        current=current_schema,
        location="evaluation manifest",
        changes=sink,
    )
    return [
        change for change in sink.changes if change.severity in ("breaking", "unknown")
    ]


def _evaluation_manifest_snapshot_version(path: Path) -> int | None:
    prefix = "evaluation_manifest_protocol_v"
    suffix = "_snapshot.json"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        return None
    raw_version = path.name[len(prefix) : -len(suffix)]
    return int(raw_version) if raw_version.isdigit() else None


def _evaluation_manifest_snapshot_paths() -> dict[int, Path]:
    snapshots: dict[int, Path] = {}
    for path in Path(__file__).parent.glob(
        "evaluation_manifest_protocol_v*_snapshot.json"
    ):
        version = _evaluation_manifest_snapshot_version(path)
        assert version is not None, f"Invalid evaluation manifest snapshot: {path.name}"
        assert version not in snapshots
        snapshots[version] = path
    return snapshots


def _git_evaluation_manifest_snapshot_texts(base_ref: str) -> dict[int, str]:
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base_ref, "--", "tests"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, (
        f"Could not inspect evaluation manifest snapshots at {base_ref!r}: "
        f"{listed.stderr.strip()}"
    )
    snapshots: dict[int, str] = {}
    for relative in listed.stdout.splitlines():
        path = Path(relative)
        version = _evaluation_manifest_snapshot_version(path)
        if path.parent != Path("tests") or version is None:
            continue
        shown = subprocess.run(
            ["git", "show", f"{base_ref}:{relative}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert shown.returncode == 0, (
            f"Could not read {relative} at {base_ref!r}: {shown.stderr.strip()}"
        )
        snapshots[version] = shown.stdout
    return snapshots


def test_evaluation_manifest_protocol_matches_its_versioned_snapshot() -> None:
    current = _render_evaluation_manifest_protocol()
    if os.environ.get(UPDATE_EVALUATION_MANIFEST_SNAPSHOT):
        if EVALUATION_MANIFEST_SNAPSHOT_PATH.exists():
            incompatible = _evaluation_manifest_incompatibilities(
                _load_evaluation_manifest_protocol(),
                current,
            )
            assert not incompatible, (
                "Breaking or unknown evaluation-manifest changes require bumping "
                f"EVALUATION_MANIFEST_VERSION: {incompatible}"
            )
        EVALUATION_MANIFEST_SNAPSHOT_PATH.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    assert EVALUATION_MANIFEST_SNAPSHOT_PATH.exists(), (
        f"No evaluation manifest v{EVALUATION_MANIFEST_VERSION} snapshot found. "
        f"Generate one with {UPDATE_EVALUATION_MANIFEST_SNAPSHOT}=1 uv run pytest "
        "tests/test_evaluations.py"
    )
    baseline = _load_evaluation_manifest_protocol()
    incompatible = _evaluation_manifest_incompatibilities(baseline, current)
    assert not incompatible, (
        "Breaking or unknown evaluation-manifest changes require bumping "
        f"EVALUATION_MANIFEST_VERSION: {incompatible}"
    )
    assert current == baseline, (
        "The evaluation manifest changed without updating its canonical snapshot. "
        f"Regenerate additive changes with {UPDATE_EVALUATION_MANIFEST_SNAPSHOT}=1 "
        "uv run pytest tests/test_evaluations.py."
    )


def test_evaluation_manifest_snapshot_history_is_complete() -> None:
    assert set(_evaluation_manifest_snapshot_paths()) == set(
        range(1, EVALUATION_MANIFEST_VERSION + 1)
    )


def test_evaluation_manifest_protocol_matches_its_git_baseline() -> None:
    base_ref = os.environ.get(EVALUATION_MANIFEST_BASE_REF)
    if base_ref is None:
        return

    baseline_snapshots = _git_evaluation_manifest_snapshot_texts(base_ref)
    if not baseline_snapshots:
        assert EVALUATION_MANIFEST_VERSION == 1
        return

    previous_version = max(baseline_snapshots)
    assert set(baseline_snapshots) == set(range(1, previous_version + 1))
    assert EVALUATION_MANIFEST_VERSION in (previous_version, previous_version + 1)

    local_snapshots = _evaluation_manifest_snapshot_paths()
    for version, baseline_text in baseline_snapshots.items():
        if version >= EVALUATION_MANIFEST_VERSION:
            continue
        assert version in local_snapshots
        assert local_snapshots[version].read_text(encoding="utf-8") == baseline_text, (
            f"Historical evaluation manifest v{version} must remain "
            "byte-for-byte stable"
        )

    if previous_version != EVALUATION_MANIFEST_VERSION:
        return
    baseline = _manifest_object(
        json.loads(baseline_snapshots[EVALUATION_MANIFEST_VERSION])
    )
    incompatible = _evaluation_manifest_incompatibilities(
        baseline,
        _render_evaluation_manifest_protocol(),
    )
    assert not incompatible, (
        "Breaking or unknown evaluation-manifest changes require bumping "
        f"EVALUATION_MANIFEST_VERSION: {incompatible}"
    )


def test_same_evaluation_manifest_version_rejects_breaking_changes() -> None:
    baseline = _render_evaluation_manifest_protocol()
    current = _manifest_object(json.loads(json.dumps(baseline)))
    schema = _manifest_object(current["manifest_schema"])
    properties = _manifest_object(schema["properties"])
    properties.pop("evaluations")

    incompatible = _evaluation_manifest_incompatibilities(baseline, current)

    assert any(change.severity == "breaking" for change in incompatible)
