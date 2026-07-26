"""Infrastructure-neutral fixed-window rate limiting.

Applications choose a stable operation namespace, an actor or tenant scope,
and a fixed-window policy. A :class:`RateLimitStore` owns the one atomic
consume operation; :func:`enforce_rate_limit` validates the policy and store
decision, then raises Tenchi's standard declared 429 response when the window
has no capacity.

The built-in :class:`MemoryRateLimitStore` is a deterministic, concurrency-safe
adapter for tests and local development. It is process-local and therefore not
a production boundary for multi-process or distributed applications.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil, isfinite
from time import monotonic
from typing import Protocol

from .errors import AppError, ConfigurationError, ErrorDef, TenchiError

_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


RATE_LIMITED = ErrorDef(
    code="RATE_LIMITED",
    status=429,
    message="Too many requests",
    headers=("Retry-After",),
)
"""Standard application error for an exhausted rate-limit window."""


class RateLimitStoreError(TenchiError, RuntimeError):
    """A rate-limit store returned an invalid decision."""


@dataclass(frozen=True, slots=True)
class RateLimitPermit:
    """An accepted cost and the capacity left in its current window."""

    limit: int
    remaining: int
    reset_after_seconds: float


@dataclass(frozen=True, slots=True)
class RateLimitExceeded:
    """A rejected cost and the delay until another attempt can be accepted."""

    limit: int
    retry_after_seconds: int


type RateLimitDecision = RateLimitPermit | RateLimitExceeded


class RateLimitStore(Protocol):
    """Atomic fixed-window consumption required by :func:`enforce_rate_limit`.

    The identity is ``(namespace, scope)``. The first accepted cost opens a
    window. Further costs are accepted while their sum does not exceed
    ``limit``; rejected costs do not consume capacity. At or after the reset,
    the next accepted cost opens a new window. Changing ``limit`` or
    ``window_seconds`` starts a new window immediately.
    """

    async def consume(
        self,
        *,
        namespace: str,
        scope: str,
        limit: int,
        window_seconds: float,
        cost: int,
    ) -> RateLimitDecision: ...


async def enforce_rate_limit(
    store: RateLimitStore,
    *,
    namespace: str,
    scope: str,
    limit: int,
    window_seconds: float,
    cost: int = 1,
) -> RateLimitPermit:
    """Consume capacity or raise ``AppError(RATE_LIMITED)``.

    ``namespace`` is a stable dotted ``snake_case`` operation name. ``scope``
    identifies the actor, tenant, credential, or other application-owned
    subject sharing the allowance. ``cost`` supports weighted operations and
    must not exceed the window limit.

    HTTP contracts that can expose a rejection must declare
    :data:`RATE_LIMITED`. The raised error includes the store's positive,
    integer ``Retry-After`` value.
    """
    validated_window = _validate_request(
        namespace=namespace,
        scope=scope,
        limit=limit,
        window_seconds=window_seconds,
        cost=cost,
        label="enforce_rate_limit",
    )
    decision = _runtime_object(
        await store.consume(
            namespace=namespace,
            scope=scope,
            limit=limit,
            window_seconds=validated_window,
            cost=cost,
        )
    )
    if isinstance(decision, RateLimitPermit):
        _validate_permit(
            decision,
            limit=limit,
            window_seconds=validated_window,
            cost=cost,
            label=f"enforce_rate_limit({namespace!r})",
        )
        return decision
    if isinstance(decision, RateLimitExceeded):
        _validate_exceeded(
            decision,
            limit=limit,
            window_seconds=validated_window,
            label=f"enforce_rate_limit({namespace!r})",
        )
        raise AppError(
            RATE_LIMITED,
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    raise RateLimitStoreError(
        f"enforce_rate_limit({namespace!r}): store.consume returned "
        f"{type(decision).__name__}, expected a rate-limit decision"
    )


@dataclass(slots=True)
class _MemoryWindow:
    limit: int
    window_seconds: float
    used: int
    resets_at: float


class MemoryRateLimitStore:
    """Process-local fixed-window storage using a finite, monotonic clock."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        if not callable(clock):
            raise ConfigurationError(
                "MemoryRateLimitStore: clock must be callable, got "
                f"{type(clock).__name__}"
            )
        self._clock = clock
        self._lock = asyncio.Lock()
        self._windows: dict[tuple[str, str], _MemoryWindow] = {}

    async def consume(
        self,
        *,
        namespace: str,
        scope: str,
        limit: int,
        window_seconds: float,
        cost: int,
    ) -> RateLimitDecision:
        validated_window = _validate_request(
            namespace=namespace,
            scope=scope,
            limit=limit,
            window_seconds=window_seconds,
            cost=cost,
            label="MemoryRateLimitStore.consume",
        )
        identity = (namespace, scope)
        async with self._lock:
            now = _clock_value(self._clock)
            self._windows = {
                key: window
                for key, window in self._windows.items()
                if window.resets_at > now
            }
            window = self._windows.get(identity)
            if window is None or (
                window.limit != limit or window.window_seconds != validated_window
            ):
                resets_at = now + validated_window
                if not isfinite(resets_at) or resets_at <= now:
                    raise RateLimitStoreError(
                        "MemoryRateLimitStore: clock and window_seconds must "
                        "produce a finite future reset"
                    )
                window = _MemoryWindow(
                    limit=limit,
                    window_seconds=validated_window,
                    used=cost,
                    resets_at=resets_at,
                )
                self._windows[identity] = window
                return RateLimitPermit(
                    limit=limit,
                    remaining=limit - cost,
                    reset_after_seconds=validated_window,
                )

            reset_after = window.resets_at - now
            if not 0 < reset_after <= validated_window:
                raise RateLimitStoreError(
                    "MemoryRateLimitStore: clock must not move backwards"
                )
            if window.used + cost > limit:
                return RateLimitExceeded(
                    limit=limit,
                    retry_after_seconds=max(1, ceil(reset_after)),
                )
            window.used += cost
            return RateLimitPermit(
                limit=limit,
                remaining=limit - window.used,
                reset_after_seconds=reset_after,
            )


def _validate_request(
    *,
    namespace: object,
    scope: object,
    limit: object,
    window_seconds: object,
    cost: object,
    label: str,
) -> float:
    if not isinstance(namespace, str) or _NAMESPACE.fullmatch(namespace) is None:
        raise ConfigurationError(
            f"{label}: namespace must use dotted snake_case, such as "
            f"'tasks.create'; got {namespace!r}"
        )
    if not isinstance(scope, str) or not scope:
        raise ConfigurationError(f"{label}: scope must be a non-empty string")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ConfigurationError(f"{label}: limit must be a positive integer")
    if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
        raise ConfigurationError(f"{label}: cost must be a positive integer")
    if cost > limit:
        raise ConfigurationError(f"{label}: cost must not exceed limit")
    if not isinstance(window_seconds, int | float) or isinstance(window_seconds, bool):
        raise ConfigurationError(
            f"{label}: window_seconds must be a finite number greater than zero"
        )
    try:
        window = float(window_seconds)
    except OverflowError as exc:
        raise ConfigurationError(
            f"{label}: window_seconds must be a finite number greater than zero"
        ) from exc
    if not isfinite(window) or window <= 0:
        raise ConfigurationError(
            f"{label}: window_seconds must be a finite number greater than zero"
        )
    return window


def _validate_permit(
    decision: RateLimitPermit,
    *,
    limit: int,
    window_seconds: float,
    cost: int,
    label: str,
) -> None:
    actual_limit = _runtime_object(decision.limit)
    remaining = _runtime_object(decision.remaining)
    reset_after = _runtime_object(decision.reset_after_seconds)
    if (
        not isinstance(actual_limit, int)
        or isinstance(actual_limit, bool)
        or actual_limit != limit
    ):
        raise RateLimitStoreError(
            f"{label}: permit limit must match the requested limit"
        )
    if (
        not isinstance(remaining, int)
        or isinstance(remaining, bool)
        or not 0 <= remaining <= limit - cost
    ):
        raise RateLimitStoreError(
            f"{label}: permit remaining must be an integer from zero through "
            "limit minus cost"
        )
    if not _valid_delay(reset_after, maximum=window_seconds):
        raise RateLimitStoreError(
            f"{label}: permit reset_after_seconds must be a finite number "
            "greater than zero and no greater than window_seconds"
        )


def _validate_exceeded(
    decision: RateLimitExceeded,
    *,
    limit: int,
    window_seconds: float,
    label: str,
) -> None:
    actual_limit = _runtime_object(decision.limit)
    retry_after = _runtime_object(decision.retry_after_seconds)
    if (
        not isinstance(actual_limit, int)
        or isinstance(actual_limit, bool)
        or actual_limit != limit
    ):
        raise RateLimitStoreError(
            f"{label}: exceeded limit must match the requested limit"
        )
    if (
        not isinstance(retry_after, int)
        or isinstance(retry_after, bool)
        or retry_after <= 0
        or retry_after > ceil(window_seconds)
    ):
        raise RateLimitStoreError(
            f"{label}: retry_after_seconds must be a positive integer no "
            "greater than the rounded-up window_seconds"
        )


def _valid_delay(value: object, *, maximum: float) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    try:
        delay = float(value)
    except OverflowError:
        return False
    return isfinite(delay) and 0 < delay <= maximum


def _clock_value(clock: Callable[[], float]) -> float:
    value = _runtime_object(clock())
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise RateLimitStoreError(
            "MemoryRateLimitStore: clock must return a finite number"
        )
    try:
        now = float(value)
    except OverflowError as exc:
        raise RateLimitStoreError(
            "MemoryRateLimitStore: clock must return a finite number"
        ) from exc
    if not isfinite(now):
        raise RateLimitStoreError(
            "MemoryRateLimitStore: clock must return a finite number"
        )
    return now


def _runtime_object(value: object) -> object:
    """Widen dataclass fields so deliberate runtime checks stay effective."""
    return value


__all__ = [
    "RATE_LIMITED",
    "MemoryRateLimitStore",
    "RateLimitDecision",
    "RateLimitExceeded",
    "RateLimitPermit",
    "RateLimitStore",
    "RateLimitStoreError",
    "enforce_rate_limit",
]
