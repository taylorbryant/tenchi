"""Request outcome observers for metrics and tracing integrations."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

import pytest
from _pytest.logging import LogCaptureFixture

from tenchi.contracts import contract
from tenchi.errors import AppError, ConfigurationError, ErrorDef
from tenchi.routes import route, route_group
from tenchi.server import RequestOutcome, create_app
from tenchi.testing import open_http

conflict = ErrorDef(code="CONFLICT", status=409, message="Conflict")


def test_observer_shape_is_checked_at_composition() -> None:
    async def ok(context: object) -> str:
        return "ok"

    def invalid() -> None:
        return None

    with pytest.raises(ConfigurationError, match=r"observer\[0\].*one positional"):
        create_app(
            routes=route_group(
                route(contract(method="GET", path="/", response=str), ok)
            ),
            context_factory=object,
            observers=(invalid,),  # type: ignore[arg-type]
        )


async def test_observers_run_in_order_after_scope_and_cannot_change_response(
    caplog: LogCaptureFixture,
) -> None:
    events: list[str] = []
    outcomes: list[RequestOutcome] = []

    @asynccontextmanager
    async def context_factory() -> AsyncGenerator[object]:
        events.append("enter")
        try:
            yield object()
        finally:
            events.append("exit")

    async def ok(context: object) -> str:
        events.append("use case")
        return "ok"

    def broken(outcome: RequestOutcome) -> None:
        events.append("broken")
        raise RuntimeError("observer failed")

    async def collect(outcome: RequestOutcome) -> None:
        events.append("collect")
        outcomes.append(outcome)

    declared = contract(method="GET", path="/ok", response=str)
    app = create_app(
        routes=route_group(route(declared, ok)),
        context_factory=context_factory,
        observers=(broken, collect),
    )

    async with open_http(app) as http:
        response = await http.get("/ok", headers={"X-Trace": "value"})

    assert response.status_code == 200
    assert response.json() == "ok"
    assert events == ["enter", "use case", "exit", "broken", "collect"]
    assert "observer failed" in caplog.text
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status_code == 200
    assert outcome.error_source is None
    assert outcome.duration_seconds >= 0
    assert outcome.request.contract is declared
    assert outcome.request.headers["x-trace"] == "value"
    assert outcome.request.request_id == response.headers["x-request-id"]


async def test_observer_sees_app_and_framework_outcomes_but_not_unmatched_routes() -> (
    None
):
    outcomes: list[RequestOutcome] = []

    async def fail(context: object) -> str:
        raise AppError(conflict)

    async def observer(outcome: RequestOutcome) -> None:
        outcomes.append(outcome)

    declared = contract(
        method="GET",
        path="/fail",
        response=str,
        errors=(conflict,),
    )
    app = create_app(
        routes=route_group(route(declared, fail)),
        context_factory=object,
        observers=(observer,),
    )

    async with open_http(app) as http:
        app_error = await http.get("/fail")
        await http.get("/missing")

    assert app_error.status_code == 409
    assert [(item.status_code, item.error_source) for item in outcomes] == [
        (409, "app")
    ]


async def test_observers_cannot_mutate_request_headers_seen_by_later_observers(
    caplog: LogCaptureFixture,
) -> None:
    seen: list[dict[str, str]] = []

    async def ok(context: object) -> str:
        return "ok"

    def mutate(outcome: RequestOutcome) -> None:
        cast(dict[str, str], outcome.request.headers)["x-injected"] = "bad"

    def collect(outcome: RequestOutcome) -> None:
        seen.append(dict(outcome.request.headers))

    declared = contract(method="GET", path="/ok", response=str)
    app = create_app(
        routes=route_group(route(declared, ok)),
        context_factory=object,
        observers=(mutate, collect),
    )

    async with open_http(app) as http:
        response = await http.get("/ok", headers={"X-Trace": "original"})

    assert response.status_code == 200
    assert "mappingproxy" in caplog.text
    assert seen[0]["x-trace"] == "original"
    assert "x-injected" not in seen[0]
