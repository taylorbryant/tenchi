"""Environment-aware, read-only deployment preflight checks.

Preflight is separate from :mod:`tenchi._checks`: ``tenchi check`` remains a
deterministic source and test gate, while preflight deliberately contacts the
environment an application is about to use.

Each check is a zero-argument async observation. Tenchi supplies no application
context and does not run the application lifespan, so checks must open their
own read-only clients and must not migrate, repair, enqueue, or otherwise
change state. Results contain only declaration-owned names, descriptions, and
failure codes. Return values and exception messages never cross the boundary.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import re
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from math import isfinite
from time import perf_counter
from typing import Any, Literal, TypeGuard, cast, get_type_hints

from .errors import ConfigurationError

type PreflightStatus = Literal["passed", "failed", "timed_out"]

_PREFLIGHT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_FAILURE_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class PreflightBindingError(ConfigurationError, TypeError):
    """A preflight declaration cannot safely invoke its check."""


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    """One named, timeout-bounded environment observation."""

    name: str
    check: Callable[[], Awaitable[None]]
    description: str | None
    timeout: float
    failure_code: str


@dataclass(frozen=True, slots=True)
class PreflightGroup:
    """A flat, ordered collection of uniquely named preflight checks."""

    checks: tuple[PreflightCheck, ...]

    def __iter__(self) -> Iterator[PreflightCheck]:
        return iter(self.checks)

    def __len__(self) -> int:
        return len(self.checks)


@dataclass(frozen=True, slots=True)
class PreflightOutcome:
    """Redacted outcome for one preflight check."""

    name: str
    description: str | None
    status: PreflightStatus
    duration_seconds: float
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Complete result of running an environment preflight."""

    outcomes: tuple[PreflightOutcome, ...]
    duration_seconds: float

    @property
    def ok(self) -> bool:
        """Whether every declared check passed."""
        return all(outcome.status == "passed" for outcome in self.outcomes)


def preflight_check(
    name: str,
    check: Callable[[], Awaitable[None]],
    *,
    description: str | None = None,
    timeout: float = 5.0,
    failure_code: str | None = None,
) -> PreflightCheck:
    """Bind a stable name to one read-only async environment observation.

    The check must accept no arguments, return ``None``, and propagate
    cancellation. Raising marks it failed; exceeding ``timeout`` marks it
    timed out. Neither the exception nor its message appears in the report.
    """
    if not _valid_name(name):
        raise PreflightBindingError(
            "preflight check name must use dotted snake_case, such as "
            f"'database.schema'; got {name!r}"
        )
    if description is not None and not _valid_description(description):
        raise PreflightBindingError(
            f"preflight_check({name!r}): description must be a non-empty string or None"
        )
    validated_timeout = _validated_timeout(
        timeout,
        label=f"preflight_check({name!r}): timeout",
    )
    resolved_failure_code = (
        _default_failure_code(name) if failure_code is None else failure_code
    )
    if not _valid_failure_code(resolved_failure_code):
        raise PreflightBindingError(
            f"preflight_check({name!r}): failure_code must use "
            f"SCREAMING_SNAKE_CASE; got {resolved_failure_code!r}"
        )
    _validate_check_callable(name, check)
    return PreflightCheck(
        name=name,
        check=check,
        description=description.strip() if description is not None else None,
        timeout=validated_timeout,
        failure_code=resolved_failure_code,
    )


def preflight_group(
    *items: PreflightCheck | PreflightGroup | Sequence[PreflightCheck],
) -> PreflightGroup:
    """Compose preflight declarations into one flat group with unique names."""
    flattened: list[PreflightCheck] = []
    for index, item in enumerate(items):
        _append_checks(flattened, item, index=index)

    seen: set[str] = set()
    for item in flattened:
        if item.name in seen:
            raise ConfigurationError(
                f"preflight_group contains duplicate check name {item.name!r}"
            )
        seen.add(item.name)
    return PreflightGroup(checks=tuple(flattened))


async def run_preflight(
    group: PreflightGroup,
    *,
    timeout: float | None = None,
) -> PreflightReport:
    """Run all checks concurrently and return declaration-order outcomes.

    ``timeout`` optionally caps every check's declared timeout; it can make a
    deployment gate stricter but never weaker. Cancelling this call cancels all
    active checks and propagates to the caller.
    """
    if not _is_preflight_group(group):
        raise ConfigurationError(
            f"run_preflight: group must be a PreflightGroup, got {type(group).__name__}"
        )
    timeout_cap = (
        None
        if timeout is None
        else _validated_timeout(timeout, label="run_preflight: timeout")
    )
    started = perf_counter()
    outcomes = await asyncio.gather(
        *(
            _run_check(
                item,
                timeout=(
                    item.timeout
                    if timeout_cap is None
                    else min(item.timeout, timeout_cap)
                ),
            )
            for item in group
        )
    )
    return PreflightReport(
        outcomes=tuple(outcomes),
        duration_seconds=_seconds_since(started),
    )


async def _run_check(
    item: PreflightCheck,
    *,
    timeout: float,
) -> PreflightOutcome:
    started = perf_counter()
    deadline = asyncio.timeout(timeout)
    try:
        async with deadline:
            result = await item.check()
        if deadline.expired():
            return _outcome(item, "timed_out", started=started)
        if result is not None:
            return _outcome(item, "failed", started=started)
    except TimeoutError:
        status: PreflightStatus = "timed_out" if deadline.expired() else "failed"
        return _outcome(item, status, started=started)
    except asyncio.CancelledError:
        raise
    except Exception:
        status = "timed_out" if deadline.expired() else "failed"
        return _outcome(item, status, started=started)
    return _outcome(item, "passed", started=started)


def _outcome(
    item: PreflightCheck,
    status: PreflightStatus,
    *,
    started: float,
) -> PreflightOutcome:
    return PreflightOutcome(
        name=item.name,
        description=item.description,
        status=status,
        duration_seconds=_seconds_since(started),
        failure_code=None if status == "passed" else item.failure_code,
    )


def _validate_check_callable(name: str, check: object) -> None:
    if not inspect.iscoroutinefunction(check):
        raise PreflightBindingError(
            f"preflight_check({name!r}): check {_describe(check)} must be "
            "an async function"
        )
    try:
        signature = inspect.signature(cast(Callable[[], Awaitable[None]], check))
    except (TypeError, ValueError) as exc:
        raise PreflightBindingError(
            f"preflight_check({name!r}): could not inspect check "
            f"{_describe(check)}: {exc}"
        ) from exc
    if signature.parameters:
        raise PreflightBindingError(
            f"preflight_check({name!r}): check {_describe(check)} must accept "
            "no arguments"
        )
    if signature.return_annotation is inspect.Signature.empty:
        raise PreflightBindingError(
            f"preflight_check({name!r}): check {_describe(check)} must declare "
            "a None return annotation"
        )
    function: Any = inspect.unwrap(check)
    while isinstance(function, functools.partial):
        function = function.func
    try:
        return_annotation = get_type_hints(function)["return"]
    except Exception as exc:
        raise PreflightBindingError(
            f"preflight_check({name!r}): could not resolve the return "
            f"annotation for {_describe(check)}: {exc}"
        ) from exc
    if return_annotation is not type(None):
        raise PreflightBindingError(
            f"preflight_check({name!r}): check {_describe(check)} must return None"
        )


def _append_checks(
    flattened: list[PreflightCheck],
    item: object,
    *,
    index: int,
) -> None:
    if isinstance(item, PreflightCheck):
        flattened.append(item)
        return
    if isinstance(item, PreflightGroup):
        flattened.extend(item.checks)
        return
    if isinstance(item, Sequence) and not isinstance(item, str | bytes):
        sequence = cast(Sequence[object], item)
        for nested_index, nested in enumerate(sequence):
            if not isinstance(nested, PreflightCheck):
                raise ConfigurationError(
                    f"preflight_group item {index}[{nested_index}] must be a "
                    f"PreflightCheck, got {type(nested).__name__}"
                )
            flattened.append(nested)
        return
    raise ConfigurationError(
        f"preflight_group item {index} must be a PreflightCheck, "
        "PreflightGroup, or sequence of PreflightCheck values, "
        f"got {type(item).__name__}"
    )


def _validated_timeout(value: object, *, label: str) -> float:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not isfinite(value)
        or value <= 0
    ):
        raise ConfigurationError(
            f"{label} must be a finite number greater than zero, got {value!r}"
        )
    return float(value)


def _valid_name(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _PREFLIGHT_NAME.fullmatch(value) is not None


def _valid_description(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _valid_failure_code(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _FAILURE_CODE.fullmatch(value) is not None


def _is_preflight_group(value: object) -> TypeGuard[PreflightGroup]:
    return isinstance(value, PreflightGroup)


def _default_failure_code(name: str) -> str:
    return f"PREFLIGHT_{name.replace('.', '_').upper()}_FAILED"


def _seconds_since(started: float) -> float:
    return max(0.0, perf_counter() - started)


def _describe(value: object) -> str:
    name = getattr(value, "__qualname__", None) or repr(value)
    module = getattr(value, "__module__", None)
    return f"{module}.{name}" if module else str(name)


__all__ = [
    "PreflightBindingError",
    "PreflightCheck",
    "PreflightGroup",
    "PreflightOutcome",
    "PreflightReport",
    "PreflightStatus",
    "preflight_check",
    "preflight_group",
    "run_preflight",
]
