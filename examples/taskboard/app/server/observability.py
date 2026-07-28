"""Low-cardinality logging and OpenTelemetry shared by every entrypoint."""

import logging

from tenchi.execution import UseCaseOutcome
from tenchi.opentelemetry import create_opentelemetry_observers
from tenchi.server import RequestOutcome

logger = logging.getLogger("taskboard.operations")
# Deployment configures the global SDK providers; local runs keep the no-op API.
telemetry = create_opentelemetry_observers()


def observe_request(outcome: RequestOutcome) -> None:
    operation = (
        outcome.request.contract.name
        or f"{outcome.request.method} {outcome.request.contract.path}"
    )
    logger.info(
        "%s -> %d in %.3fs at %s",
        operation,
        outcome.status_code,
        outcome.duration_seconds,
        outcome.completed_at.isoformat(),
    )


def observe_use_case(outcome: UseCaseOutcome) -> None:
    name = str(getattr(outcome.use_case, "__name__", type(outcome.use_case).__name__))
    logger.info(
        "%s via %s -> %s in %.3fs at %s%s",
        name,
        outcome.entrypoint,
        outcome.status,
        outcome.duration_seconds,
        outcome.completed_at.isoformat(),
        f" [{outcome.error_code}]" if outcome.error_code else "",
    )


request_observers = (telemetry.request, observe_request)
use_case_observers = (telemetry.use_case, observe_use_case)
