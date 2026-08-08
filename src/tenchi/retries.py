"""Explicit retry policy for contract-driven outbound calls.

Retries are opt-in because repeating a request is a business decision, not a
transport default. A policy names the declared application errors and raw HTTP
statuses that are transient, bounds attempts and elapsed time, and requires an
explicit opt-in before an unsafe HTTP method may be repeated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from .errors import ConfigurationError, TenchiError


class RetryTimeoutError(TenchiError, TimeoutError):
    """A client retry policy exhausted its total time budget."""

    def __init__(self, *, contract_name: str, attempts: int) -> None:
        super().__init__(
            f"{contract_name} exceeded its retry deadline after "
            f"{attempts} attempt{'s' if attempts != 1 else ''}"
        )
        self.contract_name = contract_name
        self.attempts = attempts


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    """A bounded, opt-in policy for one logical outbound client call."""

    max_attempts: int = 3
    retry_on: tuple[str, ...] = ()
    retry_on_statuses: tuple[int, ...] = ()
    retry_transport_errors: bool = True
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 5.0
    jitter: float = 0.2
    total_timeout_seconds: float | None = None
    allow_unsafe_methods: bool = False

    def __post_init__(self) -> None:
        max_attempts = cast(object, self.max_attempts)
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or max_attempts < 2
        ):
            raise ConfigurationError("RetryPolicy max_attempts must be at least 2")
        retry_on = cast(object, self.retry_on)
        if not isinstance(retry_on, tuple):
            raise ConfigurationError(
                "RetryPolicy retry_on must be a tuple of non-empty error codes"
            )
        retry_values = cast(tuple[object, ...], retry_on)
        if any(not isinstance(code, str) or not code for code in retry_values):
            raise ConfigurationError(
                "RetryPolicy retry_on must be a tuple of non-empty error codes"
            )
        if len(set(retry_values)) != len(retry_values):
            raise ConfigurationError(
                "RetryPolicy retry_on must not contain duplicate error codes"
            )
        retry_on_statuses = cast(object, self.retry_on_statuses)
        if not isinstance(retry_on_statuses, tuple):
            raise ConfigurationError(
                "RetryPolicy retry_on_statuses must be a tuple of HTTP statuses"
            )
        status_values = cast(tuple[object, ...], retry_on_statuses)
        if any(
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 400 <= status <= 599
            for status in status_values
        ):
            raise ConfigurationError(
                "RetryPolicy retry_on_statuses must contain HTTP statuses "
                "from 400 through 599"
            )
        if len(set(status_values)) != len(status_values):
            raise ConfigurationError(
                "RetryPolicy retry_on_statuses must not contain duplicate HTTP statuses"
            )
        _require_bool(
            self.retry_transport_errors,
            label="RetryPolicy retry_transport_errors",
        )
        _require_non_negative_finite(
            self.base_delay_seconds,
            label="RetryPolicy base_delay_seconds",
        )
        _require_non_negative_finite(
            self.max_delay_seconds,
            label="RetryPolicy max_delay_seconds",
        )
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ConfigurationError(
                "RetryPolicy max_delay_seconds must be greater than or equal "
                "to base_delay_seconds"
            )
        jitter = cast(object, self.jitter)
        if (
            not isinstance(jitter, int | float)
            or isinstance(jitter, bool)
            or not _is_finite(jitter)
            or not 0 <= jitter <= 1
        ):
            raise ConfigurationError("RetryPolicy jitter must be between 0 and 1")
        if self.total_timeout_seconds is not None:
            _require_positive_finite(
                self.total_timeout_seconds,
                label="RetryPolicy total_timeout_seconds",
            )
        _require_bool(
            self.allow_unsafe_methods,
            label="RetryPolicy allow_unsafe_methods",
        )


def retry_policy(
    *,
    max_attempts: int = 3,
    retry_on: Sequence[str] = (),
    retry_on_statuses: Sequence[int] = (),
    retry_transport_errors: bool = True,
    base_delay_seconds: float = 0.1,
    max_delay_seconds: float = 5.0,
    jitter: float = 0.2,
    total_timeout_seconds: float | None = None,
    allow_unsafe_methods: bool = False,
) -> RetryPolicy:
    """Declare a retry policy for an outbound client call.

    ``retry_on`` contains stable codes from errors declared by the contract
    or client. ``retry_on_statuses`` explicitly selects raw HTTP failures such
    as intermediary-generated 502, 503, or 504 responses. Unsafe methods such
    as POST require ``allow_unsafe_methods=True``; callers should normally pair
    that opt-in with an idempotency key.
    """
    raw_codes = cast(object, retry_on)
    if isinstance(raw_codes, str | bytes) or not isinstance(raw_codes, Sequence):
        raise ConfigurationError(
            "retry_policy retry_on must be a sequence of error codes"
        )
    raw_statuses = cast(object, retry_on_statuses)
    if isinstance(raw_statuses, str | bytes) or not isinstance(raw_statuses, Sequence):
        raise ConfigurationError(
            "retry_policy retry_on_statuses must be a sequence of HTTP statuses"
        )
    return RetryPolicy(
        max_attempts=max_attempts,
        retry_on=tuple(cast(Sequence[str], raw_codes)),
        retry_on_statuses=tuple(cast(Sequence[int], raw_statuses)),
        retry_transport_errors=retry_transport_errors,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        jitter=jitter,
        total_timeout_seconds=total_timeout_seconds,
        allow_unsafe_methods=allow_unsafe_methods,
    )


def _require_bool(value: object, *, label: str) -> None:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{label} must be a bool")


def _require_non_negative_finite(value: object, *, label: str) -> None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not _is_finite(value)
        or value < 0
    ):
        raise ConfigurationError(f"{label} must be a finite number at least 0")


def _require_positive_finite(value: object, *, label: str) -> None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not _is_finite(value)
        or value <= 0
    ):
        raise ConfigurationError(f"{label} must be a finite number greater than 0")


def _is_finite(value: int | float) -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


__all__ = ["RetryPolicy", "RetryTimeoutError", "retry_policy"]
