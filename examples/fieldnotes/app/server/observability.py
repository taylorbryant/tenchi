"""Payload-safe logging and optional OpenTelemetry for every entrypoint."""

import logging

from tenchi.execution import UseCaseOutcome
from tenchi.opentelemetry import create_opentelemetry_observers
from tenchi.server import RequestOutcome

logger = logging.getLogger("fieldnotes.operations")
telemetry = create_opentelemetry_observers()


def observe_request(outcome: RequestOutcome) -> None:
    operation = outcome.request.contract.name or outcome.request.contract.path
    logger.info(
        "%s -> %d in %.3fs",
        operation,
        outcome.status_code,
        outcome.duration_seconds,
    )


def observe_use_case(outcome: UseCaseOutcome) -> None:
    name = str(getattr(outcome.use_case, "__name__", type(outcome.use_case).__name__))
    logger.info(
        "%s via %s -> %s in %.3fs%s",
        name,
        outcome.entrypoint,
        outcome.status,
        outcome.duration_seconds,
        f" [{outcome.error_code}]" if outcome.error_code else "",
    )


request_observers = (telemetry.request, observe_request)
use_case_observers = (telemetry.use_case, observe_use_case)
