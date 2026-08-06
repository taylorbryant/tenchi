"""Provider-neutral evaluation gates for AI-powered application behavior.

Evaluations belong to the application. Tenchi does not choose a model, prompt
format, judge, agent loop, or dataset store. It supplies the reliable boundary
around those choices:

- cases are validated against an explicit Python type at composition and before
  every invocation;
- evaluators are exactly annotated async functions;
- one application lifespan surrounds the run and each case gets its own
  scoped context;
- concurrency and per-case timeouts are bounded;
- metric thresholds and declared token/cost budgets fail closed;
- reports contain case names, scores, usage, and stable failure codes, never
  case inputs, model outputs, prompts, or exception messages.

Evaluations are intentionally separate from deterministic source validation.
Run them explicitly before deployment or when comparing an AI implementation.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import re
from collections.abc import (
    AsyncGenerator,
    Awaitable,
    Callable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from fractions import Fraction
from math import isfinite
from time import perf_counter
from typing import Annotated, Any, Literal, cast

from jsonschema import Draft202012Validator
from pydantic import ConfigDict, Field, TypeAdapter, with_config
from typing_extensions import TypedDict, TypeForm

from .errors import ConfigurationError, TenchiError
from .execution import open_context

type EvaluationKind = Literal["deterministic", "model"]
type EvaluationCaseStatus = Literal["completed", "failed", "timed_out", "skipped"]
type EvaluationBudgetStatus = Literal["passed", "exceeded", "unverified"]

EVALUATION_MANIFEST_VERSION = 1
MAX_EVALUATION_TOKENS = 9_007_199_254_740_991

_EVALUATION_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_FAILURE_CASE = "EVALUATION_CASE_FAILED"
_FAILURE_RESULT = "EVALUATION_RESULT_INVALID"
_FAILURE_TIMEOUT = "EVALUATION_CASE_TIMED_OUT"
_FAILURE_BUDGET_EXCEEDED = "EVALUATION_BUDGET_EXCEEDED"
_FAILURE_BUDGET_UNVERIFIED = "EVALUATION_BUDGET_UNVERIFIED"

logger = logging.getLogger("tenchi.evaluations")


@with_config(ConfigDict(extra="forbid"))
class EvaluationMetricManifest(TypedDict):
    """One metric policy in a portable evaluation manifest."""

    name: Annotated[str, Field(pattern=_METRIC_NAME.pattern)]
    description: Annotated[str, Field(min_length=1)] | None
    threshold: Annotated[
        float,
        Field(ge=0, le=1, allow_inf_nan=False),
    ]


@with_config(ConfigDict(extra="forbid"))
class EvaluationManifestEntry(TypedDict):
    """One payload-free evaluation policy in a portable manifest."""

    name: Annotated[str, Field(pattern=_EVALUATION_NAME.pattern)]
    description: Annotated[str, Field(min_length=1)] | None
    kind: EvaluationKind
    case_schema: dict[str, object]
    cases: Annotated[
        list[Annotated[str, Field(pattern=_EVALUATION_NAME.pattern)]],
        Field(min_length=1),
    ]
    metrics: Annotated[list[EvaluationMetricManifest], Field(min_length=1)]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    max_tokens: (
        Annotated[
            int,
            Field(ge=1, le=MAX_EVALUATION_TOKENS),
        ]
        | None
    )
    max_cost_usd: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None


@with_config(ConfigDict(extra="forbid"))
class EvaluationManifest(TypedDict):
    """Versioned deterministic policy document for an evaluation group."""

    schema_version: Literal[1]
    evaluations: list[EvaluationManifestEntry]


class EvaluationBindingError(ConfigurationError, TypeError):
    """An evaluation declaration or evaluator cannot be composed safely."""


class EvaluationNotFoundError(TenchiError, LookupError):
    """No evaluation with the requested stable name is registered."""


class EvaluationResultError(TenchiError, TypeError):
    """An evaluator returned an invalid measurement."""


@dataclass(frozen=True, slots=True)
class EvaluationMetric:
    """One normalized score and its minimum passing average."""

    name: str
    threshold: float
    description: str | None = None

    def __post_init__(self) -> None:
        raw_name = cast(object, self.name)
        if not isinstance(raw_name, str) or _METRIC_NAME.fullmatch(raw_name) is None:
            raise EvaluationBindingError(
                "evaluation metric name must use snake_case, such as "
                f"'groundedness'; got {self.name!r}"
            )
        object.__setattr__(
            self,
            "threshold",
            _normalized_score(
                self.threshold,
                label=f"evaluation_metric({self.name!r}): threshold",
            ),
        )
        object.__setattr__(
            self,
            "description",
            _optional_description(
                self.description,
                label=f"evaluation_metric({self.name!r}): description",
            ),
        )


def evaluation_metric(
    name: str,
    *,
    threshold: float,
    description: str | None = None,
) -> EvaluationMetric:
    """Declare a normalized metric whose average must meet ``threshold``."""
    return EvaluationMetric(
        name=name,
        threshold=threshold,
        description=description,
    )


@dataclass(frozen=True, slots=True)
class EvaluationCase[CaseT]:
    """One stable, typed application-owned evaluation case."""

    name: str
    input: CaseT = field(repr=False)

    def __post_init__(self) -> None:
        raw_name = cast(object, self.name)
        if (
            not isinstance(raw_name, str)
            or _EVALUATION_NAME.fullmatch(raw_name) is None
        ):
            raise EvaluationBindingError(
                "evaluation case name must use dotted snake_case, such as "
                f"'refund.simple'; got {self.name!r}"
            )


def evaluation_case[CaseT](name: str, input: CaseT) -> EvaluationCase[CaseT]:
    """Create one named case without exposing its input in representations."""
    return EvaluationCase(name=name, input=input)


@dataclass(frozen=True, slots=True)
class EvaluationMeasurement:
    """Payload-safe scores and optional usage returned by one evaluator."""

    scores: tuple[tuple[str, float], ...]
    tokens: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        seen: set[str] = set()
        validated_scores: list[tuple[str, float]] = []
        raw_scores = cast(object, self.scores)
        if not isinstance(raw_scores, tuple):
            raise EvaluationResultError(
                "evaluation result scores must be a tuple of name/value pairs"
            )
        for index, item in enumerate(cast(tuple[object, ...], raw_scores)):
            if not isinstance(item, tuple):
                raise EvaluationResultError(
                    f"evaluation result score {index} must be a name/value pair"
                )
            pair = cast(tuple[object, ...], item)
            if len(pair) != 2:
                raise EvaluationResultError(
                    f"evaluation result score {index} must be a name/value pair"
                )
            name, value = pair
            if not isinstance(name, str) or _METRIC_NAME.fullmatch(name) is None:
                raise EvaluationResultError(
                    f"evaluation score names must use snake_case; got {name!r}"
                )
            if name in seen:
                raise EvaluationResultError(
                    f"evaluation result contains duplicate score {name!r}"
                )
            seen.add(name)
            validated_scores.append(
                (
                    name,
                    _normalized_score(
                        value,
                        label=f"evaluation score {name!r}",
                        error_type=EvaluationResultError,
                    ),
                )
            )
        if not validated_scores:
            raise EvaluationResultError(
                "evaluation result must contain at least one score"
            )
        object.__setattr__(self, "scores", tuple(validated_scores))
        object.__setattr__(
            self,
            "tokens",
            _optional_nonnegative_int(
                self.tokens,
                label="evaluation result tokens",
                error_type=EvaluationResultError,
            ),
        )
        object.__setattr__(
            self,
            "cost_usd",
            _optional_nonnegative_float(
                self.cost_usd,
                label="evaluation result cost_usd",
                error_type=EvaluationResultError,
            ),
        )


def evaluation_result(
    *,
    scores: Mapping[str, float],
    tokens: int | None = None,
    cost_usd: float | None = None,
) -> EvaluationMeasurement:
    """Return normalized scores and usage without returning evaluated payloads."""
    if not isinstance(cast(object, scores), Mapping):
        raise EvaluationResultError("evaluation result scores must be a mapping")
    return EvaluationMeasurement(
        scores=tuple(scores.items()),
        tokens=tokens,
        cost_usd=cost_usd,
    )


@dataclass(frozen=True, slots=True)
class Evaluation[CaseT, ContextT]:
    """One named suite of typed cases bound to an async evaluator."""

    name: str
    case: TypeForm[CaseT] = field(repr=False)
    cases: tuple[EvaluationCase[CaseT], ...]
    metrics: tuple[EvaluationMetric, ...]
    evaluator: Callable[[CaseT, ContextT], Awaitable[EvaluationMeasurement]] = field(
        repr=False
    )
    kind: EvaluationKind = "deterministic"
    description: str | None = None
    timeout: float = 30.0
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    _case_adapter: TypeAdapter[CaseT] = field(
        init=False, repr=False, compare=False, hash=False
    )
    _case_schema: dict[str, object] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        _validate_evaluation_name(self.name)
        object.__setattr__(
            self,
            "description",
            _optional_description(
                self.description,
                label=f"evaluation({self.name!r}): description",
            ),
        )
        if self.kind not in ("deterministic", "model"):
            raise EvaluationBindingError(
                f"evaluation({self.name!r}): kind must be 'deterministic' or "
                f"'model'; got {self.kind!r}"
            )
        metrics = _validated_metrics(self.name, tuple(self.metrics))
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(
            self,
            "timeout",
            _positive_float(
                self.timeout,
                label=f"evaluation({self.name!r}): timeout",
            ),
        )
        object.__setattr__(
            self,
            "max_tokens",
            _optional_positive_int(
                self.max_tokens,
                label=f"evaluation({self.name!r}): max_tokens",
            ),
        )
        object.__setattr__(
            self,
            "max_cost_usd",
            _optional_positive_float(
                self.max_cost_usd,
                label=f"evaluation({self.name!r}): max_cost_usd",
            ),
        )
        adapter = _build_case_adapter(self.name, self.case)
        object.__setattr__(self, "_case_adapter", adapter)
        object.__setattr__(
            self,
            "_case_schema",
            _build_case_schema(self.name, self.case, adapter),
        )
        object.__setattr__(
            self,
            "cases",
            _validated_cases(self.name, tuple(self.cases), adapter),
        )
        _validate_evaluator(self.name, self.case, self.evaluator)

    @property
    def case_schema(self) -> dict[str, object]:
        """Return a copy of the JSON Schema used to validate case inputs."""
        return deepcopy(self._case_schema)


def evaluation[CaseT, ContextT](
    name: str,
    *,
    case: TypeForm[CaseT],
    cases: Sequence[EvaluationCase[CaseT]],
    metrics: Sequence[EvaluationMetric],
    evaluator: Callable[[CaseT, ContextT], Awaitable[EvaluationMeasurement]],
    kind: EvaluationKind = "deterministic",
    description: str | None = None,
    timeout: float = 30.0,
    max_tokens: int | None = None,
    max_cost_usd: float | None = None,
) -> Evaluation[CaseT, ContextT]:
    """Declare typed cases, score thresholds, and one async evaluator."""
    return Evaluation(
        name=name,
        case=case,
        cases=tuple(cases),
        metrics=tuple(metrics),
        evaluator=evaluator,
        kind=kind,
        description=description,
        timeout=timeout,
        max_tokens=max_tokens,
        max_cost_usd=max_cost_usd,
    )


@dataclass(frozen=True, slots=True)
class EvaluationGroup:
    """A flat, ordered collection of uniquely named evaluations."""

    evaluations: tuple[Evaluation[Any, Any], ...]

    def __post_init__(self) -> None:
        raw_evaluations = cast(object, self.evaluations)
        if not isinstance(raw_evaluations, tuple):
            raise ConfigurationError(
                "EvaluationGroup evaluations must be a tuple of Evaluation "
                f"values, got {type(raw_evaluations).__name__}"
            )
        seen: set[str] = set()
        for index, item in enumerate(cast(tuple[object, ...], raw_evaluations)):
            if not isinstance(item, Evaluation):
                raise ConfigurationError(
                    f"evaluation_group item {index} must be an Evaluation, "
                    f"got {type(item).__name__}"
                )
            if item.name in seen:
                raise ConfigurationError(
                    f"evaluation_group contains duplicate evaluation name {item.name!r}"
                )
            seen.add(item.name)

    def __iter__(self) -> Iterator[Evaluation[Any, Any]]:
        return iter(self.evaluations)

    def __len__(self) -> int:
        return len(self.evaluations)


def evaluation_group(
    *items: (Evaluation[Any, Any] | EvaluationGroup | Sequence[Evaluation[Any, Any]]),
) -> EvaluationGroup:
    """Compose evaluation declarations into one flat group with unique names."""
    flattened: list[Evaluation[Any, Any]] = []
    for index, item in enumerate(items):
        _append_evaluations(flattened, item, index=index)
    return EvaluationGroup(evaluations=tuple(flattened))


def evaluation_manifest(evaluations: EvaluationGroup) -> EvaluationManifest:
    """Return a canonical payload-free manifest of evaluation policies."""
    if not isinstance(cast(object, evaluations), EvaluationGroup):
        raise ConfigurationError(
            "evaluation_manifest: evaluations must be an EvaluationGroup"
        )
    entries: list[EvaluationManifestEntry] = []
    for declared in sorted(evaluations, key=lambda item: item.name):
        entries.append(
            {
                "name": declared.name,
                "description": declared.description,
                "kind": declared.kind,
                "case_schema": declared.case_schema,
                "cases": [item.name for item in declared.cases],
                "metrics": [
                    {
                        "name": metric.name,
                        "description": metric.description,
                        "threshold": metric.threshold,
                    }
                    for metric in sorted(
                        declared.metrics,
                        key=lambda item: item.name,
                    )
                ],
                "timeout_seconds": declared.timeout,
                "max_tokens": declared.max_tokens,
                "max_cost_usd": declared.max_cost_usd,
            }
        )
    payload: EvaluationManifest = {
        "schema_version": EVALUATION_MANIFEST_VERSION,
        "evaluations": entries,
    }
    return cast(
        EvaluationManifest,
        json.loads(
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        ),
    )


@dataclass(frozen=True, slots=True)
class EvaluationCaseOutcome:
    """Redacted outcome for one named case."""

    name: str
    status: EvaluationCaseStatus
    duration_seconds: float
    scores: tuple[tuple[str, float], ...] = ()
    tokens: int | None = None
    cost_usd: float | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class EvaluationMetricOutcome:
    """Aggregate evidence for one declared metric."""

    name: str
    average: float | None
    threshold: float
    passed: bool
    samples: int


@dataclass(frozen=True, slots=True)
class EvaluationBudgetOutcome:
    """Declared and consumed usage for one evaluation."""

    max_tokens: int | None
    consumed_tokens: int | None
    max_cost_usd: float | None
    consumed_cost_usd: float | None
    status: EvaluationBudgetStatus

    @property
    def passed(self) -> bool:
        """Whether every declared budget was verified within its limit."""
        return self.status == "passed"


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """Complete payload-safe result for one evaluation."""

    name: str
    description: str | None
    kind: EvaluationKind
    duration_seconds: float
    cases: tuple[EvaluationCaseOutcome, ...]
    metrics: tuple[EvaluationMetricOutcome, ...]
    budget: EvaluationBudgetOutcome

    @property
    def ok(self) -> bool:
        return (
            bool(self.cases)
            and all(item.status == "completed" for item in self.cases)
            and all(item.passed for item in self.metrics)
            and self.budget.passed
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Complete result of an explicit evaluation run."""

    evaluations: tuple[EvaluationOutcome, ...]
    duration_seconds: float

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.evaluations)


@dataclass(frozen=True, slots=True)
class EvaluationRunner:
    """Application lifecycle, context, and bounds for an evaluation group."""

    evaluations: EvaluationGroup
    context_factory: Callable[..., Any] = field(repr=False)
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = field(
        default=None, repr=False
    )
    concurrency: int = 1
    _context_takes_state: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        raw_evaluations = cast(object, self.evaluations)
        if not isinstance(raw_evaluations, EvaluationGroup):
            raise ConfigurationError(
                "create_evaluation_runner: evaluations must be an "
                f"EvaluationGroup, got {type(self.evaluations).__name__}"
            )
        takes_state = _context_factory_takes_state(self.context_factory)
        if takes_state and self.lifespan is None:
            raise ConfigurationError(
                "create_evaluation_runner: context_factory accepts a lifespan "
                "state argument but no lifespan= was provided"
            )
        if self.lifespan is not None:
            _require_call_shape(
                self.lifespan,
                positional_arguments=0,
                label="create_evaluation_runner: lifespan",
                expectation="accept zero arguments",
            )
        object.__setattr__(
            self,
            "concurrency",
            _positive_int(
                self.concurrency,
                label="create_evaluation_runner: concurrency",
            ),
        )
        object.__setattr__(self, "_context_takes_state", takes_state)

    async def run(
        self,
        name: str | None = None,
        *,
        concurrency: int | None = None,
        timeout: float | None = None,
    ) -> EvaluationReport:
        """Run one evaluation or the complete group with bounded concurrency."""
        selected = (
            tuple(self.evaluations)
            if name is None
            else tuple(item for item in self.evaluations if item.name == name)
        )
        if name is not None and not selected:
            raise EvaluationNotFoundError(f"unknown evaluation {name!r}")
        resolved_concurrency = (
            self.concurrency
            if concurrency is None
            else _positive_int(
                concurrency,
                label="EvaluationRunner.run: concurrency",
            )
        )
        resolved_timeout = (
            None
            if timeout is None
            else _positive_float(timeout, label="EvaluationRunner.run: timeout")
        )
        started = perf_counter()
        outcomes: list[EvaluationOutcome] = []
        async with _open_lifespan(self.lifespan) as state:
            context_source = (
                functools.partial(self.context_factory, state)
                if self._context_takes_state
                else self.context_factory
            )
            for item in selected:
                outcomes.append(
                    await _run_evaluation(
                        item,
                        context_source=context_source,
                        concurrency=resolved_concurrency,
                        timeout=resolved_timeout,
                    )
                )
        return EvaluationReport(
            evaluations=tuple(outcomes),
            duration_seconds=perf_counter() - started,
        )


def create_evaluation_runner(
    *,
    evaluations: EvaluationGroup,
    context_factory: Callable[..., Any],
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    concurrency: int = 1,
) -> EvaluationRunner:
    """Compose evaluations with application lifecycle and scoped contexts."""
    return EvaluationRunner(
        evaluations=evaluations,
        context_factory=context_factory,
        lifespan=lifespan,
        concurrency=concurrency,
    )


async def _run_evaluation(
    declared: Evaluation[Any, Any],
    *,
    context_source: Callable[..., Any],
    concurrency: int,
    timeout: float | None,
) -> EvaluationOutcome:
    started = perf_counter()
    outcomes: list[EvaluationCaseOutcome] = []
    budget_status: EvaluationBudgetStatus = "passed"
    case_timeout = (
        min(declared.timeout, timeout) if timeout is not None else declared.timeout
    )

    for offset in range(0, len(declared.cases), concurrency):
        batch = declared.cases[offset : offset + concurrency]
        if budget_status != "passed":
            failure_code = (
                _FAILURE_BUDGET_EXCEEDED
                if budget_status == "exceeded"
                else _FAILURE_BUDGET_UNVERIFIED
            )
            outcomes.extend(
                EvaluationCaseOutcome(
                    name=item.name,
                    status="skipped",
                    duration_seconds=0.0,
                    failure_code=failure_code,
                )
                for item in batch
            )
            continue
        outcomes.extend(
            await asyncio.gather(
                *(
                    _run_case(
                        declared,
                        item,
                        context_source=context_source,
                        timeout=case_timeout,
                    )
                    for item in batch
                )
            )
        )
        budget = _budget_outcome(declared, outcomes)
        budget_status = budget.status

    metrics = tuple(_metric_outcome(metric, outcomes) for metric in declared.metrics)
    return EvaluationOutcome(
        name=declared.name,
        description=declared.description,
        kind=declared.kind,
        duration_seconds=perf_counter() - started,
        cases=tuple(outcomes),
        metrics=metrics,
        budget=_budget_outcome(declared, outcomes),
    )


async def _run_case(
    declared: Evaluation[Any, Any],
    item: EvaluationCase[Any],
    *,
    context_source: Callable[..., Any],
    timeout: float,
) -> EvaluationCaseOutcome:
    started = perf_counter()
    deadline = asyncio.timeout(timeout)
    try:
        case_input = _validated_case_input(
            declared.name,
            item.name,
            item.input,
            declared._case_adapter,  # pyright: ignore[reportPrivateUsage]
            error_type=EvaluationResultError,
        )
        async with deadline, open_context(context_source) as context:
            raw = await declared.evaluator(case_input, context)
            measurement = _validated_measurement(declared, raw)
        if deadline.expired():
            return EvaluationCaseOutcome(
                name=item.name,
                status="timed_out",
                duration_seconds=perf_counter() - started,
                failure_code=_FAILURE_TIMEOUT,
            )
        return EvaluationCaseOutcome(
            name=item.name,
            status="completed",
            duration_seconds=perf_counter() - started,
            scores=measurement.scores,
            tokens=measurement.tokens,
            cost_usd=measurement.cost_usd,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        if not deadline.expired():
            logger.exception(
                "Evaluation %r case %r raised a dependency timeout",
                declared.name,
                item.name,
            )
            return EvaluationCaseOutcome(
                name=item.name,
                status="failed",
                duration_seconds=perf_counter() - started,
                failure_code=_FAILURE_CASE,
            )
        return EvaluationCaseOutcome(
            name=item.name,
            status="timed_out",
            duration_seconds=perf_counter() - started,
            failure_code=_FAILURE_TIMEOUT,
        )
    except EvaluationResultError:
        logger.exception(
            "Evaluation %r case %r returned an invalid result",
            declared.name,
            item.name,
        )
        return EvaluationCaseOutcome(
            name=item.name,
            status="failed",
            duration_seconds=perf_counter() - started,
            failure_code=_FAILURE_RESULT,
        )
    except Exception:
        logger.exception(
            "Evaluation %r case %r failed unexpectedly",
            declared.name,
            item.name,
        )
        return EvaluationCaseOutcome(
            name=item.name,
            status="failed",
            duration_seconds=perf_counter() - started,
            failure_code=_FAILURE_CASE,
        )


def _validated_measurement(
    declared: Evaluation[Any, Any],
    value: object,
) -> EvaluationMeasurement:
    if not isinstance(value, EvaluationMeasurement):
        raise EvaluationResultError(
            f"evaluation({declared.name!r}) returned {type(value).__name__}; "
            "expected EvaluationMeasurement"
        )
    expected = {metric.name for metric in declared.metrics}
    actual = {name for name, _ in value.scores}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing scores {missing}")
        if extra:
            details.append(f"undeclared scores {extra}")
        raise EvaluationResultError(
            f"evaluation({declared.name!r}) result does not match declared "
            f"metrics: {', '.join(details)}"
        )
    if declared.max_tokens is not None and value.tokens is None:
        raise EvaluationResultError(
            f"evaluation({declared.name!r}) declares max_tokens but its result "
            "did not report tokens"
        )
    if declared.max_cost_usd is not None and value.cost_usd is None:
        raise EvaluationResultError(
            f"evaluation({declared.name!r}) declares max_cost_usd but its result "
            "did not report cost_usd"
        )
    return value


def _metric_outcome(
    metric: EvaluationMetric,
    outcomes: Sequence[EvaluationCaseOutcome],
) -> EvaluationMetricOutcome:
    values = [
        dict(item.scores)[metric.name]
        for item in outcomes
        if item.status == "completed" and metric.name in dict(item.scores)
    ]
    average = sum(values) / len(values) if values else None
    return EvaluationMetricOutcome(
        name=metric.name,
        average=average,
        threshold=metric.threshold,
        passed=average is not None and average >= metric.threshold,
        samples=len(values),
    )


def _budget_outcome(
    declared: Evaluation[Any, Any],
    outcomes: Sequence[EvaluationCaseOutcome],
) -> EvaluationBudgetOutcome:
    attempted = [item for item in outcomes if item.status != "skipped"]
    completed = [item for item in attempted if item.status == "completed"]
    tokens_known = all(
        item.status == "completed" and item.tokens is not None for item in attempted
    )
    costs_known = all(
        item.status == "completed" and item.cost_usd is not None for item in attempted
    )
    raw_consumed_tokens = (
        sum(cast(int, item.tokens) for item in completed)
        if attempted and tokens_known
        else 0
        if not attempted
        else None
    )
    consumed_tokens = (
        raw_consumed_tokens
        if raw_consumed_tokens is None or raw_consumed_tokens <= MAX_EVALUATION_TOKENS
        else None
    )
    exact_consumed_cost = (
        _exact_cost_sum(completed)
        if attempted and costs_known
        else Fraction(0)
        if not attempted
        else None
    )
    consumed_cost = _json_safe_cost(exact_consumed_cost)
    token_status = _budget_dimension_status(
        declared.max_tokens,
        raw_consumed_tokens,
    )
    cost_status = _cost_budget_dimension_status(
        declared.max_cost_usd,
        exact_consumed_cost,
    )
    status: EvaluationBudgetStatus
    if "exceeded" in (token_status, cost_status):
        status = "exceeded"
    elif "unverified" in (token_status, cost_status):
        status = "unverified"
    else:
        status = "passed"
    return EvaluationBudgetOutcome(
        max_tokens=declared.max_tokens,
        consumed_tokens=consumed_tokens,
        max_cost_usd=declared.max_cost_usd,
        consumed_cost_usd=consumed_cost,
        status=status,
    )


def _budget_dimension_status(
    limit: int | float | None,
    consumed: int | float | None,
) -> EvaluationBudgetStatus:
    if limit is None:
        return "passed"
    if consumed is None:
        return "unverified"
    return "passed" if consumed <= limit else "exceeded"


def _cost_budget_dimension_status(
    limit: float | None,
    consumed: Fraction | None,
) -> EvaluationBudgetStatus:
    if limit is None:
        return "passed"
    if consumed is None:
        return "unverified"
    return "passed" if consumed <= Fraction(str(limit)) else "exceeded"


def _exact_cost_sum(
    outcomes: Sequence[EvaluationCaseOutcome],
) -> Fraction:
    return sum(
        (Fraction(str(cast(float, item.cost_usd))) for item in outcomes),
        start=Fraction(0),
    )


def _json_safe_cost(value: Fraction | None) -> float | None:
    if value is None:
        return None
    try:
        resolved = float(value)
    except OverflowError:
        return None
    return resolved if isfinite(resolved) else None


def _validate_evaluation_name(name: object) -> None:
    if not isinstance(name, str) or _EVALUATION_NAME.fullmatch(name) is None:
        raise EvaluationBindingError(
            "evaluation name must use dotted snake_case, such as "
            f"'support.answer_quality'; got {name!r}"
        )


def _validated_metrics(
    name: str,
    metrics: tuple[EvaluationMetric, ...],
) -> tuple[EvaluationMetric, ...]:
    if not metrics:
        raise EvaluationBindingError(
            f"evaluation({name!r}): metrics must contain at least one metric"
        )
    seen: set[str] = set()
    for index, item in enumerate(metrics):
        raw_item = cast(object, item)
        if not isinstance(raw_item, EvaluationMetric):
            raise EvaluationBindingError(
                f"evaluation({name!r}): metric {index} must be an "
                f"EvaluationMetric, got {type(item).__name__}"
            )
        if item.name in seen:
            raise EvaluationBindingError(
                f"evaluation({name!r}): duplicate metric {item.name!r}"
            )
        seen.add(item.name)
    return metrics


def _build_case_adapter[CaseT](
    name: str,
    annotation: TypeForm[CaseT],
) -> TypeAdapter[CaseT]:
    try:
        return cast(TypeAdapter[CaseT], TypeAdapter(cast(Any, annotation)))
    except Exception as exc:
        raise EvaluationBindingError(
            f"evaluation({name!r}): Pydantic cannot use case type "
            f"{_type_name(annotation)}: {exc}"
        ) from exc


def _build_case_schema[CaseT](
    name: str,
    annotation: TypeForm[CaseT],
    adapter: TypeAdapter[CaseT],
) -> dict[str, object]:
    try:
        schema = adapter.json_schema(mode="validation", by_alias=True)
        canonical = cast(
            dict[str, object],
            json.loads(
                json.dumps(
                    schema,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            ),
        )
        Draft202012Validator.check_schema(canonical)
        return canonical
    except Exception as exc:
        raise EvaluationBindingError(
            f"evaluation({name!r}): Pydantic cannot use case type "
            f"{_type_name(annotation)}: {exc}"
        ) from exc


def _validated_cases[CaseT](
    name: str,
    cases: tuple[EvaluationCase[CaseT], ...],
    adapter: TypeAdapter[CaseT],
) -> tuple[EvaluationCase[CaseT], ...]:
    if not cases:
        raise EvaluationBindingError(
            f"evaluation({name!r}): cases must contain at least one case"
        )
    seen: set[str] = set()
    validated: list[EvaluationCase[CaseT]] = []
    for index, item in enumerate(cases):
        raw_item = cast(object, item)
        if not isinstance(raw_item, EvaluationCase):
            raise EvaluationBindingError(
                f"evaluation({name!r}): case {index} must be an EvaluationCase, "
                f"got {type(item).__name__}"
            )
        if item.name in seen:
            raise EvaluationBindingError(
                f"evaluation({name!r}): duplicate case name {item.name!r}"
            )
        seen.add(item.name)
        validated.append(
            EvaluationCase(
                name=item.name,
                input=_validated_case_input(
                    name,
                    item.name,
                    item.input,
                    adapter,
                    error_type=EvaluationBindingError,
                ),
            )
        )
    return tuple(validated)


def _validated_case_input[CaseT](
    evaluation_name: str,
    case_name: str,
    value: CaseT,
    adapter: TypeAdapter[CaseT],
    *,
    error_type: type[Exception],
) -> CaseT:
    isolated = _isolated_case_input(
        evaluation_name,
        case_name,
        value,
        error_type=error_type,
    )
    try:
        initially_validated = adapter.validate_python(isolated)
        representation = adapter.dump_python(
            initially_validated,
            mode="python",
            round_trip=True,
            warnings="error",
        )
        validated = adapter.validate_python(representation)
    except Exception as exc:
        raise error_type(
            f"evaluation({evaluation_name!r}): case {case_name!r} does not "
            "match the declared case type"
        ) from exc
    return _isolated_case_input(
        evaluation_name,
        case_name,
        validated,
        error_type=error_type,
    )


def _isolated_case_input[CaseT](
    evaluation_name: str,
    case_name: str,
    value: CaseT,
    *,
    error_type: type[Exception],
) -> CaseT:
    try:
        return deepcopy(value)
    except Exception as exc:
        raise error_type(
            f"evaluation({evaluation_name!r}): case {case_name!r} input "
            "cannot be isolated for evaluation"
        ) from exc


def _validate_evaluator(
    name: str,
    case: object,
    evaluator: Callable[..., Awaitable[Any]],
) -> None:
    if not inspect.iscoroutinefunction(evaluator):
        raise EvaluationBindingError(
            f"evaluation({name!r}): evaluator {_describe(evaluator)} must be "
            "an async function"
        )
    try:
        signature = inspect.signature(evaluator)
    except (TypeError, ValueError) as exc:
        raise EvaluationBindingError(
            f"evaluation({name!r}): could not inspect evaluator "
            f"{_describe(evaluator)}: {exc}"
        ) from exc
    parameters = tuple(signature.parameters.values())
    if len(parameters) != 2:
        raise EvaluationBindingError(
            f"evaluation({name!r}): evaluator {_describe(evaluator)} must accept "
            "exactly 'case' and 'context'"
        )
    for expected, parameter in zip(("case", "context"), parameters, strict=True):
        if parameter.name != expected:
            raise EvaluationBindingError(
                f"evaluation({name!r}): evaluator {_describe(evaluator)} "
                f"parameter {parameter.name!r} must be named {expected!r}"
            )
        if parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD,):
            raise EvaluationBindingError(
                f"evaluation({name!r}): evaluator parameter {expected!r} must "
                "accept a positional argument"
            )
        if parameter.annotation is inspect.Parameter.empty:
            raise EvaluationBindingError(
                f"evaluation({name!r}): evaluator parameter {expected!r} must "
                "be annotated"
            )
    case_annotation = _resolved_annotation(
        name,
        evaluator,
        parameters[0].annotation,
        label="case",
    )
    if case_annotation != case:
        raise EvaluationBindingError(
            f"evaluation({name!r}): evaluator {_describe(evaluator)} case "
            f"annotation {_type_name(case_annotation)} does not match declared "
            f"case type {_type_name(case)}"
        )
    result_annotation = _resolved_annotation(
        name,
        evaluator,
        signature.return_annotation,
        label="return",
    )
    if result_annotation is not EvaluationMeasurement:
        raise EvaluationBindingError(
            f"evaluation({name!r}): evaluator {_describe(evaluator)} return "
            f"annotation {_type_name(result_annotation)} must be "
            "EvaluationMeasurement"
        )


def _resolved_annotation(
    name: str,
    function: Callable[..., Any],
    annotation: object,
    *,
    label: str,
) -> object:
    if annotation is inspect.Signature.empty:
        raise EvaluationBindingError(
            f"evaluation({name!r}): evaluator {_describe(function)} {label} "
            "must be annotated"
        )
    candidate: Any = function
    while isinstance(candidate, functools.partial):
        candidate = candidate.func
    candidate = inspect.unwrap(candidate)
    namespace = getattr(candidate, "__globals__", {})
    original = annotation
    seen: set[str] = set()
    while isinstance(annotation, str):
        if annotation in seen:
            raise EvaluationBindingError(
                f"evaluation({name!r}): could not resolve {label} annotation "
                f"{original!r}: cyclic forward reference"
            )
        seen.add(annotation)
        try:
            annotation = eval(annotation, namespace)
        except Exception as exc:
            raise EvaluationBindingError(
                f"evaluation({name!r}): could not resolve {label} annotation "
                f"{original!r}: {exc}"
            ) from exc
    return annotation


def _append_evaluations(
    flattened: list[Evaluation[Any, Any]],
    item: object,
    *,
    index: int,
) -> None:
    if isinstance(item, Evaluation):
        flattened.append(cast(Evaluation[Any, Any], item))
        return
    if isinstance(item, EvaluationGroup):
        flattened.extend(item.evaluations)
        return
    if isinstance(item, Sequence) and not isinstance(item, str | bytes):
        sequence = cast(Sequence[object], item)
        for nested_index, nested in enumerate(sequence):
            if not isinstance(nested, Evaluation):
                raise ConfigurationError(
                    f"evaluation_group item {index}[{nested_index}] must be an "
                    f"Evaluation, got {type(nested).__name__}"
                )
            flattened.append(cast(Evaluation[Any, Any], nested))
        return
    raise ConfigurationError(
        f"evaluation_group item {index} must be an Evaluation, EvaluationGroup, "
        f"or sequence of Evaluation values, got {type(item).__name__}"
    )


def _context_factory_takes_state(factory: Callable[..., Any]) -> bool:
    _require_call_shape(
        factory,
        positional_arguments=(0, 1),
        label="create_evaluation_runner: context_factory",
        expectation="accept zero arguments, or one lifespan state argument",
    )
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError) as exc:  # pragma: no cover - guarded above
        raise ConfigurationError(
            f"create_evaluation_runner: could not inspect context_factory: {exc}"
        ) from exc
    required = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return len(required) == 1


def _require_call_shape(
    function: Callable[..., Any],
    *,
    positional_arguments: int | tuple[int, ...],
    label: str,
    expectation: str,
) -> None:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} could not be inspected: {exc}") from exc
    counts = (
        (positional_arguments,)
        if isinstance(positional_arguments, int)
        else positional_arguments
    )
    for count in counts:
        try:
            signature.bind(*([object()] * count))
        except TypeError:
            continue
        return
    raise ConfigurationError(f"{label} must {expectation}")


@asynccontextmanager
async def _open_lifespan(
    lifespan: Callable[[], AbstractAsyncContextManager[Any]] | None,
) -> AsyncGenerator[Any]:
    if lifespan is None:
        yield None
        return
    async with lifespan() as state:
        yield state


def _normalized_score(
    value: object,
    *,
    label: str,
    error_type: type[Exception] = EvaluationBindingError,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise error_type(f"{label} must be a number from 0 to 1")
    try:
        resolved = float(value)
    except OverflowError as exc:
        raise error_type(f"{label} must be a finite number from 0 to 1") from exc
    if not isfinite(resolved) or not 0 <= resolved <= 1:
        raise error_type(f"{label} must be a finite number from 0 to 1")
    return resolved


def _positive_float(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvaluationBindingError(f"{label} must be a number greater than zero")
    try:
        resolved = float(value)
    except OverflowError as exc:
        raise EvaluationBindingError(
            f"{label} must be a finite number greater than zero"
        ) from exc
    if not isfinite(resolved) or resolved <= 0:
        raise EvaluationBindingError(
            f"{label} must be a finite number greater than zero"
        )
    return resolved


def _positive_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvaluationBindingError(f"{label} must be an integer greater than zero")
    return value


def _optional_positive_int(value: object, *, label: str) -> int | None:
    if value is None:
        return None
    resolved = _positive_int(value, label=label)
    if resolved > MAX_EVALUATION_TOKENS:
        raise EvaluationBindingError(
            f"{label} must be no greater than {MAX_EVALUATION_TOKENS}"
        )
    return resolved


def _optional_positive_float(value: object, *, label: str) -> float | None:
    if value is None:
        return None
    return _positive_float(value, label=label)


def _optional_nonnegative_int(
    value: object,
    *,
    label: str,
    error_type: type[Exception],
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise error_type(f"{label} must be a non-negative integer or None")
    if value > MAX_EVALUATION_TOKENS:
        raise error_type(
            f"{label} must be no greater than {MAX_EVALUATION_TOKENS} or None"
        )
    return value


def _optional_nonnegative_float(
    value: object,
    *,
    label: str,
    error_type: type[Exception],
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise error_type(f"{label} must be a non-negative finite number or None")
    try:
        resolved = float(value)
    except OverflowError as exc:
        raise error_type(
            f"{label} must be a non-negative finite number or None"
        ) from exc
    if not isfinite(resolved) or resolved < 0:
        raise error_type(f"{label} must be a non-negative finite number or None")
    return resolved


def _optional_description(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvaluationBindingError(f"{label} must be a non-empty string or None")
    return value.strip()


def _describe(value: object) -> str:
    module = getattr(value, "__module__", None)
    name = getattr(value, "__qualname__", getattr(value, "__name__", None))
    if isinstance(module, str) and isinstance(name, str):
        return f"{module}.{name}"
    return repr(value)


def _type_name(value: object) -> str:
    return getattr(value, "__name__", repr(value))


__all__ = [
    "EVALUATION_MANIFEST_VERSION",
    "MAX_EVALUATION_TOKENS",
    "Evaluation",
    "EvaluationBindingError",
    "EvaluationBudgetOutcome",
    "EvaluationBudgetStatus",
    "EvaluationCase",
    "EvaluationCaseOutcome",
    "EvaluationCaseStatus",
    "EvaluationGroup",
    "EvaluationKind",
    "EvaluationManifest",
    "EvaluationManifestEntry",
    "EvaluationMeasurement",
    "EvaluationMetric",
    "EvaluationMetricManifest",
    "EvaluationMetricOutcome",
    "EvaluationNotFoundError",
    "EvaluationOutcome",
    "EvaluationReport",
    "EvaluationResultError",
    "EvaluationRunner",
    "create_evaluation_runner",
    "evaluation",
    "evaluation_case",
    "evaluation_group",
    "evaluation_manifest",
    "evaluation_metric",
    "evaluation_result",
]
