"""Optional OpenTelemetry recording over Tenchi's payload-safe outcomes."""

import asyncio
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricsData
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from tenchi import __version__
from tenchi.client import ClientAttemptOutcome, ClientOutcome
from tenchi.contracts import contract
from tenchi.execution import UseCaseOutcome
from tenchi.opentelemetry import (
    OpenTelemetryObservers,
    create_opentelemetry_observers,
)
from tenchi.routes import route, route_group
from tenchi.server import RequestInfo, RequestOutcome, create_app
from tenchi.testing import open_http


def _providers() -> tuple[
    TracerProvider,
    MeterProvider,
    InMemorySpanExporter,
    InMemoryMetricReader,
]:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider(shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    metrics = InMemoryMetricReader()
    meter_provider = MeterProvider(
        metric_readers=(metrics,),
        shutdown_on_exit=False,
    )
    return tracer_provider, meter_provider, spans, metrics


def _metric_points(data: MetricsData) -> dict[str, tuple[Any, ...]]:
    result: dict[str, tuple[Any, ...]] = {}
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                points = cast(Any, metric.data).data_points
                result[metric.name] = tuple(points)
    return result


def _attributes(span: ReadableSpan) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], span.attributes or {})


def test_records_each_outcome_without_payload_or_correlation_values() -> None:
    tracer_provider, meter_provider, exporter, reader = _providers()
    observers = create_opentelemetry_observers(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
    declared = contract(
        method="GET",
        path="/items/{item_id}",
        response=str,
        name="get_item",
    )
    completed_at = datetime(2026, 7, 27, 12, tzinfo=UTC)

    async def read_item(context: object) -> str:
        return "secret response"

    observers.request(
        RequestOutcome(
            request=RequestInfo(
                method="GET",
                path="/items/private-item",
                headers={"authorization": "secret-token"},
                contract=declared,
                request_id="secret-request-id",
            ),
            status_code=403,
            duration_seconds=0.25,
            error_source="app",
            completed_at=completed_at,
        )
    )
    observers.use_case(
        UseCaseOutcome(
            use_case=read_item,
            entrypoint="job",
            status="app_error",
            duration_seconds=0.5,
            error_code="ITEM_PRIVATE",
            completed_at=completed_at,
        )
    )
    observers.client(
        ClientOutcome(
            contract=declared,
            status="app_error",
            status_code=404,
            duration_seconds=0.75,
            error_code="ITEM_MISSING",
            attempts=2,
            completed_at=completed_at,
        )
    )
    observers.client_attempt(
        ClientAttemptOutcome(
            contract=declared,
            attempt=1,
            max_attempts=2,
            status="transport_error",
            status_code=None,
            duration_seconds=1.0,
            will_retry=True,
            retry_delay_seconds=0.1,
            completed_at=completed_at,
        )
    )

    finished = exporter.get_finished_spans()
    use_case_name = (
        f"tenchi.use_case {__name__}."
        "test_records_each_outcome_without_payload_or_correlation_values."
        "<locals>.read_item"
    )
    assert {span.name for span in finished} == {
        "tenchi.request get_item",
        use_case_name,
        "tenchi.client get_item",
        "tenchi.client.attempt get_item",
    }
    assert all(span.kind.name == "INTERNAL" for span in finished)
    assert all(span.status.status_code is StatusCode.ERROR for span in finished)
    for span in finished:
        scope = span.instrumentation_scope
        assert scope is not None
        assert scope.name == "tenchi"
        assert scope.version == __version__
    expected_end = round(completed_at.timestamp() * 1_000_000_000)
    for span in finished:
        start_time = span.start_time
        end_time = span.end_time
        assert start_time is not None
        assert end_time is not None
        assert end_time == expected_end
        assert end_time >= start_time
    request_span = next(
        span for span in finished if span.name.startswith("tenchi.request")
    )
    request_start = request_span.start_time
    request_end = request_span.end_time
    assert request_start is not None
    assert request_end is not None
    assert request_end - request_start == 250_000_000

    rendered_attributes = repr([_attributes(span) for span in finished])
    assert "secret-token" not in rendered_attributes
    assert "private-item" not in rendered_attributes
    assert "secret-request-id" not in rendered_attributes
    assert "secret response" not in rendered_attributes
    assert "/items/{item_id}" in rendered_attributes
    assert "ITEM_PRIVATE" in rendered_attributes
    assert "ITEM_MISSING" in rendered_attributes
    client_span = next(
        span for span in finished if span.name == "tenchi.client get_item"
    )
    assert _attributes(client_span)["tenchi.client.attempts"] == 2
    attempt_span = next(
        span for span in finished if span.name == "tenchi.client.attempt get_item"
    )
    assert _attributes(attempt_span)["tenchi.client.attempt"] == 1
    assert _attributes(attempt_span)["tenchi.client.max_attempts"] == 2
    assert _attributes(attempt_span)["tenchi.client.retry_delay_seconds"] == 0.1

    data = reader.get_metrics_data()
    assert data is not None
    points = _metric_points(data)
    assert set(points) == {
        "tenchi.http.server.requests",
        "tenchi.http.server.request.duration",
        "tenchi.use_case.invocations",
        "tenchi.use_case.duration",
        "tenchi.http.client.calls",
        "tenchi.http.client.call.duration",
        "tenchi.http.client.attempts",
        "tenchi.http.client.attempt.duration",
    }
    rendered_metrics = repr(points)
    assert "secret-token" not in rendered_metrics
    assert "private-item" not in rendered_metrics
    assert "secret-request-id" not in rendered_metrics
    assert "ITEM_PRIVATE" not in rendered_metrics
    assert "ITEM_MISSING" not in rendered_metrics
    assert "'tenchi.client.will_retry': True" in rendered_metrics
    request_point = points["tenchi.http.server.request.duration"][0]
    assert len(request_point.exemplars) == 1
    request_context = request_span.context
    assert request_context is not None
    assert request_point.exemplars[0].span_id == request_context.span_id

    tracer_provider.shutdown()
    meter_provider.shutdown()


async def test_observers_wire_into_http_and_share_the_current_trace() -> None:
    tracer_provider, meter_provider, exporter, reader = _providers()
    observers = create_opentelemetry_observers(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )

    async def greet(context: object) -> str:
        return "hello"

    declared = contract(
        method="GET",
        path="/greet",
        response=str,
        name="greet",
    )
    app = create_app(
        routes=route_group(route(declared, greet)),
        context_factory=object,
        observers=(observers.request,),
        use_case_observers=(observers.use_case,),
    )
    tracer = tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("parent") as parent:
        async with open_http(app) as http:
            response = await http.get("/greet")
        parent_span_id = parent.get_span_context().span_id

    assert response.status_code == 200
    finished = exporter.get_finished_spans()
    children = [span for span in finished if span.name != "parent"]
    assert len(children) == 2
    assert all(span.parent is not None for span in children)
    assert {span.parent.span_id for span in children if span.parent is not None} == {
        parent_span_id
    }
    assert all(span.status.status_code is StatusCode.UNSET for span in children)

    data = reader.get_metrics_data()
    assert data is not None
    points = _metric_points(data)
    assert points["tenchi.http.server.requests"][0].value == 1
    assert points["tenchi.http.server.request.duration"][0].count == 1
    assert points["tenchi.use_case.invocations"][0].value == 1

    tracer_provider.shutdown()
    meter_provider.shutdown()


async def test_request_completion_precedes_use_case_observer_delivery() -> None:
    delivery_started: list[datetime] = []
    requests: list[RequestOutcome] = []

    async def greet(context: object) -> str:
        return "hello"

    async def delay(outcome: UseCaseOutcome) -> None:
        delivery_started.append(datetime.now(UTC))
        await asyncio.sleep(0)

    declared = contract(
        method="GET",
        path="/greet",
        response=str,
        name="greet",
    )
    app = create_app(
        routes=route_group(route(declared, greet)),
        context_factory=object,
        observers=(requests.append,),
        use_case_observers=(delay,),
    )

    async with open_http(app) as http:
        response = await http.get("/greet")

    assert response.status_code == 200
    assert len(requests) == 1
    assert len(delivery_started) == 1
    assert requests[0].completed_at <= delivery_started[0]


def test_observer_bundle_is_frozen() -> None:
    observers = create_opentelemetry_observers()
    with pytest.raises(FrozenInstanceError):
        observers.request = lambda outcome: None  # type: ignore[misc]
    assert isinstance(observers, OpenTelemetryObservers)
