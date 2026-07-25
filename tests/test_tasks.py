"""Validated operational tasks."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

from tenchi.errors import AppError, ConfigurationError, ErrorDef
from tenchi.execution import UseCaseOutcome
from tenchi.tasks import (
    TaskBindingError,
    TaskNotFoundError,
    TaskResultError,
    create_task_runner,
    task,
    task_group,
)


class BackfillInput(BaseModel):
    limit: int
    dry_run: bool = True


class BackfillResult(BaseModel):
    examined: int
    changed: int


@dataclass(frozen=True, slots=True)
class Context:
    events: list[str]


async def backfill(request: BackfillInput, context: Context) -> BackfillResult:
    context.events.append(f"run:{request.limit}:{request.dry_run}")
    return BackfillResult(examined=request.limit, changed=0)


async def ping(context: Context) -> str:
    context.events.append("ping")
    return "pong"


def test_task_declaration_exposes_validated_schemas() -> None:
    declared = task(
        "projects.backfill_slugs",
        backfill,
        description="Populate missing project slugs.",
    )

    assert declared.name == "projects.backfill_slugs"
    assert declared.request is BackfillInput
    assert declared.result is BackfillResult
    assert declared.request_required
    assert declared.description == "Populate missing project slugs."


@pytest.mark.parametrize("name", ["Bad", "bad-name", ".bad", "bad.", "bad..name"])
def test_task_names_are_stable_dotted_snake_case(name: str) -> None:
    with pytest.raises(TaskBindingError, match="dotted snake_case"):
        task(name, ping)


def test_task_binding_rejects_invalid_use_cases_at_composition() -> None:
    def sync(context: Context) -> str:
        return "no"

    async def no_context() -> str:
        return "no"

    async def no_return(context: Context):  # type: ignore[no-untyped-def]
        return None

    with pytest.raises(TaskBindingError, match="async function"):
        task("sync", sync)  # type: ignore[arg-type]
    with pytest.raises(TaskBindingError, match="must accept a 'context'"):
        task("no_context", no_context)
    with pytest.raises(TaskBindingError, match="return must be annotated"):
        task("no_return", no_return)


def test_task_groups_flatten_and_reject_duplicate_names() -> None:
    first = task("one", ping)
    second = task("two", ping)

    assert task_group(first, task_group(second)).tasks == (first, second)
    with pytest.raises(ConfigurationError, match="duplicate task name 'one'"):
        task_group(first, first)


async def test_runner_validates_input_before_opening_resources() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[list[str]]:
        events.append("lifespan:enter")
        yield events
        events.append("lifespan:exit")

    def context_factory(state: list[str]) -> Context:
        state.append("context:create")
        return Context(state)

    runner = create_task_runner(
        tasks=task_group(task("backfill", backfill)),
        context_factory=context_factory,
        lifespan=lifespan,
    )

    with pytest.raises(ValidationError):
        await runner.run("backfill", input={"limit": "not-an-int"})

    assert events == []


async def test_runner_owns_lifespan_context_and_validated_result() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[list[str]]:
        events.append("lifespan:enter")
        try:
            yield events
        finally:
            events.append("lifespan:exit")

    @asynccontextmanager
    async def context_factory(state: list[str]) -> AsyncGenerator[Context]:
        state.append("context:enter")
        try:
            yield Context(state)
            state.append("context:commit")
        except BaseException:
            state.append("context:rollback")
            raise

    runner = create_task_runner(
        tasks=task_group(task("backfill", backfill)),
        context_factory=context_factory,
        lifespan=lifespan,
    )

    result = await runner.run(
        "backfill",
        input_json='{"limit": 3, "dry_run": false}',
    )

    assert result == BackfillResult(examined=3, changed=0)
    assert events == [
        "lifespan:enter",
        "context:enter",
        "run:3:False",
        "context:commit",
        "lifespan:exit",
    ]


async def test_invalid_result_rolls_back_the_context() -> None:
    events: list[str] = []

    async def wrong(context: Context) -> str:
        context.events.append("run")
        return 42  # type: ignore[return-value]

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(events)
            events.append("context:commit")
        except BaseException:
            events.append("context:rollback")
            raise

    runner = create_task_runner(
        tasks=task_group(task("wrong", wrong)),
        context_factory=context_factory,
    )

    with pytest.raises(TaskResultError, match="task 'wrong' returned"):
        await runner.run("wrong")

    assert events == ["context:enter", "run", "context:rollback"]


async def test_defaulted_and_absent_inputs_are_distinct() -> None:
    async def defaulted(
        request: int = 7,
        *,
        context: Context,
    ) -> int:
        return request

    runner = create_task_runner(
        tasks=task_group(task("defaulted", defaulted), task("ping", ping)),
        context_factory=lambda: Context([]),
    )

    assert await runner.run("defaulted") == 7
    assert await runner.run("defaulted", input=3) == 3
    assert await runner.run("ping") == "pong"
    with pytest.raises(TaskBindingError, match="does not declare"):
        await runner.run("ping", input=None)


async def test_runner_reports_unknown_names_and_missing_input() -> None:
    runner = create_task_runner(
        tasks=task_group(task("backfill", backfill)),
        context_factory=lambda: Context([]),
    )

    with pytest.raises(TaskNotFoundError, match="unknown task 'missing'"):
        await runner.run("missing")
    with pytest.raises(TaskBindingError, match="requires input"):
        await runner.run("backfill")


def test_runner_validates_composition_eagerly() -> None:
    async def lifespan_with_argument(value: object) -> AsyncGenerator[object]:
        yield value

    def stateful_context(state: object) -> object:
        return state

    with pytest.raises(ConfigurationError, match="no lifespan"):
        create_task_runner(
            tasks=task_group(),
            context_factory=stateful_context,
        )
    with pytest.raises(ConfigurationError, match=r"lifespan.*zero arguments"):
        create_task_runner(
            tasks=task_group(),
            context_factory=lambda: object(),
            lifespan=lifespan_with_argument,  # type: ignore[arg-type]
        )


async def test_task_calls_share_use_case_observability() -> None:
    events: list[str] = []
    outcomes: list[UseCaseOutcome] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("context:enter")
        try:
            yield Context(events)
        finally:
            events.append("context:exit")

    def observe(outcome: UseCaseOutcome) -> None:
        events.append(f"observe:{outcome.entrypoint}:{outcome.status}")
        outcomes.append(outcome)

    runner = create_task_runner(
        tasks=task_group(task("ping", ping)),
        context_factory=context_factory,
        use_case_observers=(observe,),
    )

    assert await runner.run("ping") == "pong"
    assert events == [
        "context:enter",
        "ping",
        "context:exit",
        "observe:task:succeeded",
    ]
    assert outcomes[0].use_case is ping


async def test_app_errors_and_cancellation_cross_the_task_boundary() -> None:
    rejected = ErrorDef(code="REJECTED", status=409, message="Rejected")
    outcomes: list[UseCaseOutcome] = []

    async def reject(context: Context) -> None:
        raise AppError(rejected)

    async def wait(context: Context) -> None:
        await asyncio.Event().wait()

    runner = create_task_runner(
        tasks=task_group(task("reject", reject), task("wait", wait)),
        context_factory=lambda: Context([]),
        use_case_observers=(outcomes.append,),
    )

    with pytest.raises(AppError):
        await runner.run("reject")
    pending = asyncio.create_task(runner.run("wait"))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert [outcome.status for outcome in outcomes] == ["app_error", "cancelled"]
