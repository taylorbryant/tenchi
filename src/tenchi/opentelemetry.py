"""OpenTelemetry spans and metrics for Tenchi's finalized outcomes.

Install the optional ``otel`` extra before importing this module. Applications
own the OpenTelemetry SDK, resources, processors, readers, and exporters;
Tenchi depends only on the stable API and records through the providers the
application supplies.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    from opentelemetry import metrics, trace
    from opentelemetry.metrics import Counter, Histogram, Meter, MeterProvider
    from opentelemetry.trace import (
        Span,
        SpanKind,
        Status,
        StatusCode,
        Tracer,
        TracerProvider,
    )
except ImportError as exc:
    if exc.name == "opentelemetry" or (
        exc.name is not None and exc.name.startswith("opentelemetry.")
    ):
        raise ImportError(
            "tenchi.opentelemetry requires OpenTelemetry support; "
            'run: uv add "tenchi[otel]"'
        ) from exc
    raise

from . import __version__
from .client import (
    ClientAttemptObserver,
    ClientAttemptOutcome,
    ClientObserver,
    ClientOutcome,
)
from .execution import UseCaseObserver, UseCaseOutcome
from .server import OutcomeObserver, RequestOutcome

_INSTRUMENTATION_NAME = "tenchi"

_CONTRACT_NAME = "tenchi.contract.name"
_CONTRACT_PATH = "tenchi.contract.path"
_ENTRYPOINT = "tenchi.entrypoint"
_ERROR_CODE = "tenchi.error.code"
_ERROR_SOURCE = "tenchi.error.source"
_HTTP_METHOD = "http.request.method"
_HTTP_ROUTE = "http.route"
_HTTP_STATUS_CODE = "http.response.status_code"
_OUTCOME_STATUS = "tenchi.outcome.status"
_USE_CASE_NAME = "tenchi.use_case.name"
_ATTEMPT = "tenchi.client.attempt"
_ATTEMPTS = "tenchi.client.attempts"
_MAX_ATTEMPTS = "tenchi.client.max_attempts"
_WILL_RETRY = "tenchi.client.will_retry"
_RETRY_DELAY_SECONDS = "tenchi.client.retry_delay_seconds"
_DURATION_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.25,
    0.5,
    0.75,
    1,
    2.5,
    5,
    7.5,
    10,
)


@dataclass(frozen=True, slots=True)
class OpenTelemetryObservers:
    """Observer bundle for Tenchi's HTTP, use-case, and client boundaries.

    Register the four callables with the matching Tenchi composition points.
    They contain no request or response payloads, raw URLs, headers, exception
    objects, or request ids.
    """

    request: OutcomeObserver
    use_case: UseCaseObserver
    client: ClientObserver
    client_attempt: ClientAttemptObserver


def create_opentelemetry_observers(
    *,
    tracer_provider: TracerProvider | None = None,
    meter_provider: MeterProvider | None = None,
) -> OpenTelemetryObservers:
    """Create payload-safe logical spans and bounded-cardinality metrics.

    When providers are omitted, the process-global OpenTelemetry providers are
    used. Without an application-configured SDK they are no-ops, as required by
    the OpenTelemetry API.
    """
    tracer = trace.get_tracer(
        _INSTRUMENTATION_NAME,
        instrumenting_library_version=__version__,
        tracer_provider=tracer_provider,
    )
    meter = metrics.get_meter(
        _INSTRUMENTATION_NAME,
        version=__version__,
        meter_provider=meter_provider,
    )
    recorder = _Recorder(tracer=tracer, meter=meter)
    return OpenTelemetryObservers(
        request=recorder.observe_request,
        use_case=recorder.observe_use_case,
        client=recorder.observe_client,
        client_attempt=recorder.observe_client_attempt,
    )


class _Recorder:
    __slots__ = (
        "_client_attempt_duration",
        "_client_attempts",
        "_client_call_duration",
        "_client_calls",
        "_request_duration",
        "_requests",
        "_tracer",
        "_use_case_duration",
        "_use_case_invocations",
    )

    def __init__(self, *, tracer: Tracer, meter: Meter) -> None:
        self._tracer = tracer
        self._requests = meter.create_counter(
            "tenchi.http.server.requests",
            unit="{request}",
            description="Completed matched Tenchi HTTP requests.",
        )
        self._request_duration = meter.create_histogram(
            "tenchi.http.server.request.duration",
            unit="s",
            description="Duration of completed matched Tenchi HTTP requests.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self._use_case_invocations = meter.create_counter(
            "tenchi.use_case.invocations",
            unit="{invocation}",
            description="Completed Tenchi use-case invocations.",
        )
        self._use_case_duration = meter.create_histogram(
            "tenchi.use_case.duration",
            unit="s",
            description="Time spent inside Tenchi use-case functions.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self._client_calls = meter.create_counter(
            "tenchi.http.client.calls",
            unit="{call}",
            description="Completed logical Tenchi client calls.",
        )
        self._client_call_duration = meter.create_histogram(
            "tenchi.http.client.call.duration",
            unit="s",
            description="Duration of completed logical Tenchi client calls.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self._client_attempts = meter.create_counter(
            "tenchi.http.client.attempts",
            unit="{attempt}",
            description="Completed Tenchi client transport attempts.",
        )
        self._client_attempt_duration = meter.create_histogram(
            "tenchi.http.client.attempt.duration",
            unit="s",
            description="Duration of completed Tenchi client transport attempts.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )

    def observe_request(self, outcome: RequestOutcome) -> None:
        contract = outcome.request.contract
        attributes: dict[str, Any] = {
            _CONTRACT_NAME: contract.name,
            _HTTP_METHOD: outcome.request.method,
            _HTTP_ROUTE: contract.path,
            _HTTP_STATUS_CODE: outcome.status_code,
            _OUTCOME_STATUS: _request_status(outcome),
        }
        if outcome.error_source is not None:
            attributes[_ERROR_SOURCE] = outcome.error_source
        self._record(
            tracer_name=f"tenchi.request {contract.name}",
            duration_seconds=outcome.duration_seconds,
            completed_at=outcome.completed_at,
            status=_request_status(outcome),
            attributes=attributes,
            counter=self._requests,
            histogram=self._request_duration,
        )

    def observe_use_case(self, outcome: UseCaseOutcome) -> None:
        name = _callable_name(outcome.use_case)
        metric_attributes: dict[str, Any] = {
            _USE_CASE_NAME: name,
            _ENTRYPOINT: outcome.entrypoint,
            _OUTCOME_STATUS: outcome.status,
        }
        span_attributes = dict(metric_attributes)
        if outcome.error_code is not None:
            span_attributes[_ERROR_CODE] = outcome.error_code
        self._record(
            tracer_name=f"tenchi.use_case {name}",
            duration_seconds=outcome.duration_seconds,
            completed_at=outcome.completed_at,
            status=outcome.status,
            attributes=span_attributes,
            metric_attributes=metric_attributes,
            counter=self._use_case_invocations,
            histogram=self._use_case_duration,
        )

    def observe_client(self, outcome: ClientOutcome) -> None:
        contract = outcome.contract
        metric_attributes: dict[str, Any] = {
            _CONTRACT_NAME: contract.name,
            _CONTRACT_PATH: contract.path,
            _HTTP_METHOD: contract.method,
            _OUTCOME_STATUS: outcome.status,
        }
        if outcome.status_code is not None:
            metric_attributes[_HTTP_STATUS_CODE] = outcome.status_code
        span_attributes = dict(metric_attributes)
        if outcome.error_code is not None:
            span_attributes[_ERROR_CODE] = outcome.error_code
        span_attributes[_ATTEMPTS] = outcome.attempts
        self._record(
            tracer_name=f"tenchi.client {contract.name}",
            duration_seconds=outcome.duration_seconds,
            completed_at=outcome.completed_at,
            status=outcome.status,
            attributes=span_attributes,
            metric_attributes=metric_attributes,
            counter=self._client_calls,
            histogram=self._client_call_duration,
        )

    def observe_client_attempt(self, outcome: ClientAttemptOutcome) -> None:
        contract = outcome.contract
        metric_attributes: dict[str, Any] = {
            _CONTRACT_NAME: contract.name,
            _CONTRACT_PATH: contract.path,
            _HTTP_METHOD: contract.method,
            _OUTCOME_STATUS: outcome.status,
            _WILL_RETRY: outcome.will_retry,
        }
        if outcome.status_code is not None:
            metric_attributes[_HTTP_STATUS_CODE] = outcome.status_code
        span_attributes = {
            **metric_attributes,
            _ATTEMPT: outcome.attempt,
            _MAX_ATTEMPTS: outcome.max_attempts,
        }
        if outcome.error_code is not None:
            span_attributes[_ERROR_CODE] = outcome.error_code
        if outcome.retry_delay_seconds is not None:
            span_attributes[_RETRY_DELAY_SECONDS] = outcome.retry_delay_seconds
        self._record(
            tracer_name=f"tenchi.client.attempt {contract.name}",
            duration_seconds=outcome.duration_seconds,
            completed_at=outcome.completed_at,
            status=outcome.status,
            attributes=span_attributes,
            metric_attributes=metric_attributes,
            counter=self._client_attempts,
            histogram=self._client_attempt_duration,
        )

    def _record(
        self,
        *,
        tracer_name: str,
        duration_seconds: float,
        completed_at: datetime,
        status: str,
        attributes: dict[str, Any],
        counter: Counter,
        histogram: Histogram,
        metric_attributes: dict[str, Any] | None = None,
    ) -> None:
        measurement_attributes = (
            attributes if metric_attributes is None else metric_attributes
        )
        ended_at = _unix_nano(completed_at)
        span = self._tracer.start_span(
            tracer_name,
            kind=SpanKind.INTERNAL,
            attributes=attributes,
            start_time=ended_at - max(0, round(duration_seconds * 1_000_000_000)),
        )
        try:
            context = trace.set_span_in_context(span)
            counter.add(1, measurement_attributes, context=context)
            histogram.record(
                duration_seconds,
                measurement_attributes,
                context=context,
            )
        finally:
            _finish_span(span, status=status, ended_at=ended_at)


def _finish_span(span: Span, *, status: str, ended_at: int) -> None:
    if status not in ("succeeded", "success"):
        span.set_status(Status(StatusCode.ERROR, status))
    span.end(end_time=ended_at)


def _unix_nano(value: datetime) -> int:
    return round(value.timestamp() * 1_000_000_000)


def _request_status(outcome: RequestOutcome) -> str:
    if outcome.error_source is not None:
        return f"{outcome.error_source}_error"
    return "succeeded"


def _callable_name(value: Callable[..., Any]) -> str:
    module = getattr(value, "__module__", None)
    name = getattr(value, "__qualname__", None)
    if isinstance(module, str) and isinstance(name, str):
        return f"{module}.{name}"
    if isinstance(name, str):
        return name
    return type(value).__qualname__


__all__ = [
    "OpenTelemetryObservers",
    "create_opentelemetry_observers",
]
