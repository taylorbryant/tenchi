"""Use-case outcomes shared by HTTP and direct execution."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError, dataclass
from typing import cast

import pytest
from _pytest.logging import LogCaptureFixture
from pydantic import BaseModel, ValidationError

from tenchi.contracts import contract
from tenchi.errors import AppError, ConfigurationError, ErrorDef
from tenchi.execution import (
    ExecutionError,
    UseCaseObserver,
    UseCaseOutcome,
    execute,
)
from tenchi.routes import route, route_group
from tenchi.server import RequestOutcome, create_app
from tenchi.testing import open_http


@dataclass(frozen=True, slots=True)
class Context:
    events: list[str]


class Message(BaseModel):
    text: str


conflict = ErrorDef(code="CONFLICT", status=409, message="Conflict")


async def echo(request: Message, context: Context) -> Message:
    context.events.append("use case")
    return request


async def reject(context: Context) -> str:
    context.events.append("reject")
    raise AppError(conflict)


async def crash(context: Context) -> str:
    context.events.append("crash")
    raise RuntimeError("boom")


async def cancel(context: Context) -> str:
    context.events.append("cancel")
    raise asyncio.CancelledError


def test_observer_shape_is_checked_before_composition_or_execution() -> None:
    def invalid() -> None:
        return None

    declared = contract(method="GET", path="/", response=str)

    with pytest.raises(
        ConfigurationError,
        match=r"use_case_observers\[0\].*one positional",
    ):
        create_app(
            routes=route_group(route(declared, reject)),
            context_factory=lambda: Context([]),
            use_case_observers=(invalid,),  # type: ignore[arg-type]
        )


async def test_execute_rejects_invalid_observer_before_opening_context() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("enter")
        yield Context(events)

    def invalid() -> None:
        return None

    with pytest.raises(
        ExecutionError,
        match=r"use_case_observers\[0\].*one positional",
    ):
        await execute(
            echo,
            request={"text": "hello"},
            context=context_factory,
            use_case_observers=(invalid,),  # type: ignore[arg-type]
        )

    assert events == []


async def test_one_observer_contract_covers_http_and_execute_after_scope_cleanup() -> (
    None
):
    events: list[str] = []
    outcomes: list[UseCaseOutcome] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(events)
        finally:
            events.append("exit")

    async def observe(outcome: UseCaseOutcome) -> None:
        events.append(f"observe {outcome.entrypoint}")
        outcomes.append(outcome)

    declared = contract(
        method="POST",
        path="/echo",
        request=Message,
        response=Message,
    )
    app = create_app(
        routes=route_group(route(declared, echo)),
        context_factory=context_factory,
        use_case_observers=(observe,),
    )

    async with open_http(app) as http:
        response = await http.post("/echo", json={"text": "http"})

    assert response.status_code == 200
    assert events == ["enter", "use case", "exit", "observe http"]

    events.clear()
    direct = await execute(
        echo,
        request={"text": "direct"},
        context=context_factory,
        use_case_observers=(observe,),
    )

    assert direct == Message(text="direct")
    assert events == ["enter", "use case", "exit", "observe execute"]
    assert [
        (
            outcome.use_case,
            outcome.entrypoint,
            outcome.status,
            outcome.error_code,
        )
        for outcome in outcomes
    ] == [
        (echo, "http", "succeeded", None),
        (echo, "execute", "succeeded", None),
    ]
    assert all(outcome.duration_seconds >= 0 for outcome in outcomes)


@pytest.mark.parametrize(
    ("use_case", "error", "status", "error_code"),
    [
        (reject, AppError, "app_error", "CONFLICT"),
        (crash, RuntimeError, "failed", None),
        (cancel, asyncio.CancelledError, "cancelled", None),
    ],
)
async def test_execute_classifies_failures(
    use_case: object,
    error: type[BaseException],
    status: str,
    error_code: str | None,
) -> None:
    outcomes: list[UseCaseOutcome] = []

    with pytest.raises(error):
        await execute(
            use_case,  # pyright: ignore[reportArgumentType]
            context=Context([]),
            use_case_observers=(outcomes.append,),
        )

    assert len(outcomes) == 1
    assert outcomes[0].status == status
    assert outcomes[0].error_code == error_code


@pytest.mark.parametrize(
    ("use_case", "errors", "response_status", "status", "error_code"),
    [
        (reject, (conflict,), 409, "app_error", "CONFLICT"),
        (crash, (), 500, "failed", None),
    ],
)
async def test_http_classifies_use_case_failures(
    use_case: object,
    errors: tuple[ErrorDef, ...],
    response_status: int,
    status: str,
    error_code: str | None,
) -> None:
    outcomes: list[UseCaseOutcome] = []
    declared = contract(
        method="GET",
        path="/failure",
        response=str,
        errors=errors,
    )
    app = create_app(
        routes=route_group(
            route(
                declared,
                use_case,  # pyright: ignore[reportArgumentType]
            )
        ),
        context_factory=lambda: Context([]),
        use_case_observers=(outcomes.append,),
    )

    async with open_http(app) as http:
        response = await http.get("/failure")

    assert response.status_code == response_status
    assert len(outcomes) == 1
    assert outcomes[0].entrypoint == "http"
    assert outcomes[0].status == status
    assert outcomes[0].error_code == error_code


async def test_validation_failure_does_not_report_an_uninvoked_use_case() -> None:
    outcomes: list[UseCaseOutcome] = []

    with pytest.raises(ValidationError):
        await execute(
            echo,
            request={},
            context=Context([]),
            use_case_observers=(outcomes.append,),
        )

    assert outcomes == []

    declared = contract(
        method="POST",
        path="/echo",
        request=Message,
        response=Message,
    )
    app = create_app(
        routes=route_group(route(declared, echo)),
        context_factory=lambda: Context([]),
        use_case_observers=(outcomes.append,),
    )

    async with open_http(app) as http:
        response = await http.post("/echo", json={})

    assert response.status_code == 422
    assert outcomes == []


async def test_context_acquisition_failure_does_not_report_an_uninvoked_use_case() -> (
    None
):
    outcomes: list[UseCaseOutcome] = []

    def broken_context() -> Context:
        raise RuntimeError("context unavailable")

    with pytest.raises(RuntimeError, match="context unavailable"):
        await execute(
            reject,
            context=broken_context,
            use_case_observers=(outcomes.append,),
        )

    assert outcomes == []


async def test_http_reports_use_case_success_after_response_failure_rolls_back() -> (
    None
):
    events: list[str] = []
    outcomes: list[UseCaseOutcome] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[Context]:
        events.append("enter")
        try:
            yield Context(events)
        except BaseException:
            events.append("rollback")
            raise

    async def wrong(context: Context) -> str:
        events.append("use case")
        return 42  # type: ignore[return-value]

    declared = contract(method="GET", path="/wrong", response=str)
    app = create_app(
        routes=route_group(route(declared, wrong)),
        context_factory=context_factory,
        use_case_observers=(outcomes.append,),
    )

    async with open_http(app) as http:
        response = await http.get("/wrong")

    assert response.status_code == 500
    assert events == ["enter", "use case", "rollback"]
    assert len(outcomes) == 1
    assert outcomes[0].status == "succeeded"


async def test_observer_failures_are_isolated_and_outcomes_are_frozen(
    caplog: LogCaptureFixture,
) -> None:
    events: list[str] = []
    outcomes: list[UseCaseOutcome] = []

    def mutate(outcome: UseCaseOutcome) -> None:
        events.append("mutate")
        cast(UseCaseOutcome, outcome).status = "failed"  # type: ignore[misc]

    def broken(outcome: UseCaseOutcome) -> None:
        events.append("broken")
        raise RuntimeError("observer failed")

    async def collect(outcome: UseCaseOutcome) -> None:
        events.append("collect")
        outcomes.append(outcome)

    observers: tuple[UseCaseObserver, ...] = (mutate, broken, collect)
    result = await execute(
        echo,
        request={"text": "ok"},
        context=Context(events),
        use_case_observers=observers,
    )

    assert result == Message(text="ok")
    assert events == ["use case", "mutate", "broken", "collect"]
    assert len(outcomes) == 1
    assert outcomes[0].status == "succeeded"
    assert "FrozenInstanceError" in caplog.text
    assert "observer failed" in caplog.text
    with pytest.raises(FrozenInstanceError):
        outcomes[0].status = "failed"  # type: ignore[misc]


async def test_observer_failure_does_not_replace_use_case_failure(
    caplog: LogCaptureFixture,
) -> None:
    def broken(outcome: UseCaseOutcome) -> None:
        raise LookupError(outcome.status)

    with pytest.raises(RuntimeError, match="boom"):
        await execute(
            crash,
            context=Context([]),
            use_case_observers=(broken,),
        )

    assert "LookupError: failed" in caplog.text


async def test_use_case_observer_time_does_not_inflate_request_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 10.0
    request_outcomes: list[RequestOutcome] = []

    def clock() -> float:
        return now

    async def timed(context: object) -> str:
        nonlocal now
        now = 12.0
        return "ok"

    async def slow_observer(outcome: UseCaseOutcome) -> None:
        nonlocal now
        now = 110.0

    monkeypatch.setattr("tenchi.server.perf_counter", clock)
    declared = contract(method="GET", path="/timed", response=str)
    app = create_app(
        routes=route_group(route(declared, timed)),
        context_factory=lambda: object(),
        observers=(request_outcomes.append,),
        use_case_observers=(slow_observer,),
    )

    async with open_http(app) as http:
        response = await http.get("/timed")

    assert response.status_code == 200
    assert len(request_outcomes) == 1
    assert request_outcomes[0].duration_seconds == 2.0
