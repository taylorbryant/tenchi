"""Shared mechanics for Tenchi's reusable store conformance checks."""

from __future__ import annotations

import inspect
from asyncio import CancelledError
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from .idempotency import IdempotencyStore
from .rate_limits import RateLimitStore

type ClockAdvance = Callable[[float], Awaitable[None] | None]
type IdempotencyStoreFactory = Callable[
    [],
    AbstractAsyncContextManager[IdempotencyStore],
]
type RateLimitStoreFactory = Callable[
    [],
    AbstractAsyncContextManager[RateLimitStore],
]


class StoreConformanceError(AssertionError):
    """One required store behavior failed its reusable conformance case."""

    def __init__(
        self,
        *,
        store: str,
        case: str,
        reason: str,
    ) -> None:
        super().__init__(f"{store} conformance case {case!r} failed: {reason}")
        self.store = store
        self.case = case
        self.reason = reason


class CaseFailure(AssertionError):
    """An adapter returned a decision that violates the current case."""


async def advance_clock(advance: ClockAdvance, seconds: float) -> None:
    result = advance(seconds)
    if inspect.isawaitable(result):
        await result


async def run_case(
    store: str,
    case: str,
    operation: Callable[[], Awaitable[None]],
) -> None:
    try:
        await operation()
    except (CancelledError, KeyboardInterrupt):
        raise
    except StoreConformanceError:
        raise
    except CaseFailure as exc:
        raise StoreConformanceError(
            store=store,
            case=case,
            reason=str(exc),
        ) from exc
    except Exception as exc:
        reason = f"adapter raised {type(exc).__name__}"
        if str(exc):
            reason = f"{reason}: {exc}"
        raise StoreConformanceError(
            store=store,
            case=case,
            reason=reason,
        ) from exc


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise CaseFailure(reason)


def require_type[ExpectedT](
    value: object,
    expected: type[ExpectedT],
    reason: str,
) -> ExpectedT:
    if not isinstance(value, expected):
        raise CaseFailure(reason)
    return value


def require_callable(value: object, *, label: str) -> None:
    if not callable(value):
        raise TypeError(f"{label} must be callable, got {type(value).__name__}")


__all__ = [
    "CaseFailure",
    "ClockAdvance",
    "IdempotencyStoreFactory",
    "RateLimitStoreFactory",
    "StoreConformanceError",
    "advance_clock",
    "require",
    "require_callable",
    "require_type",
    "run_case",
]
