"""Infrastructure-neutral idempotency for retry-safe operations.

Tenchi owns the small state machine while applications own durable storage
and transaction scope. A store reserves a key, returns a completed replay,
or reports a conflict/in-progress operation. ``run_idempotently()`` validates
and serializes the operation result so every replay crosses the same typed
boundary.

The store that protects a database write should participate in the same
transaction as that write. A separate cache cannot make an application
mutation and its idempotency record atomic.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from math import isfinite
from typing import Any, Protocol, cast

from pydantic import TypeAdapter

from .errors import AppError, ConfigurationError, ErrorDef, TenchiError

logger = logging.getLogger("tenchi.idempotency")

_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


IDEMPOTENCY_CONFLICT = ErrorDef(
    code="IDEMPOTENCY_CONFLICT",
    status=409,
    message="The idempotency key was already used with different input",
)
"""Standard application error for a key reused with different input."""


IDEMPOTENCY_IN_PROGRESS = ErrorDef(
    code="IDEMPOTENCY_IN_PROGRESS",
    status=409,
    message="An operation with this idempotency key is already in progress",
    headers=("Retry-After",),
)
"""Standard application error for matching work that has not completed."""


class IdempotencyResultError(TenchiError, TypeError):
    """An operation or stored replay violates its declared result type."""


class IdempotencyStoreError(TenchiError, RuntimeError):
    """An idempotency store violated the required transition contract."""


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    """Exclusive permission to execute one logical operation."""

    namespace: str
    scope: str
    key: str
    fingerprint: str
    token: str
    completed_ttl: float | None


@dataclass(frozen=True, slots=True)
class IdempotencyReplay:
    """A completed result serialized with the operation's declared type."""

    result_json: bytes


@dataclass(frozen=True, slots=True)
class IdempotencyConflict:
    """The scoped key already belongs to different validated input."""


@dataclass(frozen=True, slots=True)
class IdempotencyInProgress:
    """Matching input is currently executing under another reservation."""

    retry_after_seconds: int | None = None


type IdempotencyDecision = (
    IdempotencyReservation
    | IdempotencyReplay
    | IdempotencyConflict
    | IdempotencyInProgress
)


class IdempotencyStore(Protocol):
    """Durable state transitions required by :func:`run_idempotently`.

    ``reserve`` must atomically identify one winner for a scoped key and
    fingerprint. ``complete`` must settle only the matching reservation token;
    ``abandon`` must release only that token and be safe to repeat.
    """

    async def reserve(
        self,
        *,
        namespace: str,
        scope: str,
        key: str,
        fingerprint: str,
        completed_ttl: float | None,
        reservation_ttl: float,
    ) -> IdempotencyDecision: ...

    async def complete(
        self,
        reservation: IdempotencyReservation,
        *,
        result_json: bytes,
    ) -> None: ...

    async def abandon(self, reservation: IdempotencyReservation) -> None: ...


def fingerprint(value: Any, *, annotation: Any = _UNSET) -> str:
    """Return a stable SHA-256 fingerprint of validated JSON-compatible data.

    Pass ``annotation=`` when the value should first be validated against a
    declared request type. Without it, Pydantic uses the value's runtime type.
    Mapping keys are sorted and insignificant JSON whitespace is removed, so
    equivalent validated data produces the same fingerprint.
    """
    declared: Any = cast(Any, type(value)) if annotation is _UNSET else annotation
    adapter = _build_adapter(declared, label="fingerprint annotation")
    validated = adapter.validate_python(value)
    python_value = adapter.dump_python(
        validated,
        mode="python",
        by_alias=True,
    )
    if _contains_unordered_collection(python_value):
        raise ConfigurationError(
            "fingerprint: unordered set and frozenset values are not supported; "
            "convert them to a sorted tuple or list"
        )
    try:
        jsonable = adapter.dump_python(
            validated,
            mode="json",
            by_alias=True,
        )
        canonical = json.dumps(
            jsonable,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            "fingerprint: value must have a canonical JSON representation"
        ) from exc
    return hashlib.sha256(canonical).hexdigest()


async def run_idempotently[ResultT](
    store: IdempotencyStore,
    *,
    namespace: str,
    scope: str,
    key: str,
    fingerprint: str,
    result_type: Any,
    operation: Callable[[], Awaitable[ResultT]],
    completed_ttl: float | None = None,
    reservation_ttl: float = 300.0,
) -> ResultT:
    """Run or replay one typed logical operation.

    ``namespace``, ``scope``, and ``key`` form the durable lookup identity.
    The fingerprint identifies the validated logical input. The result is
    validated against ``result_type`` before completion and again on replay.

    A conflict or active matching reservation raises :class:`AppError` using
    ``IDEMPOTENCY_CONFLICT`` or ``IDEMPOTENCY_IN_PROGRESS``. HTTP contracts
    must declare those definitions for Tenchi's normal error-honesty rule.
    Operation failures and cancellation abandon the reservation before they
    propagate.
    """
    _validate_identity(
        namespace=namespace,
        scope=scope,
        key=key,
        fingerprint=fingerprint,
    )
    validated_completed_ttl = _validated_ttl(
        completed_ttl,
        label="run_idempotently: completed_ttl",
        optional=True,
    )
    validated_reservation_ttl = cast(
        float,
        _validated_ttl(
            reservation_ttl,
            label="run_idempotently: reservation_ttl",
            optional=False,
        ),
    )
    adapter = _build_adapter(result_type, label="run_idempotently result_type")

    decision: object = await store.reserve(
        namespace=namespace,
        scope=scope,
        key=key,
        fingerprint=fingerprint,
        completed_ttl=validated_completed_ttl,
        reservation_ttl=validated_reservation_ttl,
    )
    if isinstance(decision, IdempotencyReplay):
        replay_json = _runtime_object(decision.result_json)
        if not isinstance(replay_json, bytes):
            raise IdempotencyStoreError(
                f"run_idempotently({namespace!r}): replay result_json must be bytes"
            )
        try:
            return cast(ResultT, adapter.validate_json(replay_json))
        except Exception as exc:
            raise IdempotencyResultError(
                f"run_idempotently({namespace!r}): stored replay does not "
                "match result_type"
            ) from exc
    if isinstance(decision, IdempotencyConflict):
        raise AppError(IDEMPOTENCY_CONFLICT)
    if isinstance(decision, IdempotencyInProgress):
        retry_after = _runtime_object(decision.retry_after_seconds)
        if retry_after is not None and (
            not isinstance(retry_after, int)
            or isinstance(retry_after, bool)
            or retry_after <= 0
        ):
            raise IdempotencyStoreError(
                f"run_idempotently({namespace!r}): in-progress "
                "retry_after_seconds must be a positive integer or None"
            )
        headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
        raise AppError(IDEMPOTENCY_IN_PROGRESS, headers=headers)
    reservation = _require_reservation(
        decision,
        namespace=namespace,
        scope=scope,
        key=key,
        fingerprint=fingerprint,
        completed_ttl=validated_completed_ttl,
    )
    try:
        raw_result = await operation()
        try:
            result = adapter.validate_python(raw_result)
            result_json = adapter.dump_json(result, by_alias=True)
        except Exception as exc:
            raise IdempotencyResultError(
                f"run_idempotently({namespace!r}): operation result does not "
                "match result_type"
            ) from exc
        await store.complete(reservation, result_json=result_json)
        return cast(ResultT, result)
    except BaseException:
        await _abandon_without_masking(store, reservation)
        raise


async def _abandon_without_masking(
    store: IdempotencyStore,
    reservation: IdempotencyReservation,
) -> None:
    try:
        await store.abandon(reservation)
    except BaseException:
        logger.exception(
            "Failed to abandon idempotency reservation for %s",
            reservation.namespace,
        )


def _require_reservation(
    decision: object,
    *,
    namespace: str,
    scope: str,
    key: str,
    fingerprint: str,
    completed_ttl: float | None,
) -> IdempotencyReservation:
    if not isinstance(decision, IdempotencyReservation):
        raise IdempotencyStoreError(
            f"run_idempotently({namespace!r}): store.reserve returned "
            f"{type(decision).__name__}, expected an idempotency decision"
        )
    expected = (namespace, scope, key, fingerprint, completed_ttl)
    actual = (
        decision.namespace,
        decision.scope,
        decision.key,
        decision.fingerprint,
        decision.completed_ttl,
    )
    if actual != expected:
        raise IdempotencyStoreError(
            f"run_idempotently({namespace!r}): store returned a reservation "
            "for a different identity, fingerprint, or completed TTL"
        )
    token = _runtime_object(decision.token)
    if not isinstance(token, str) or not token:
        raise IdempotencyStoreError(
            f"run_idempotently({namespace!r}): reservation token must be "
            "a non-empty string"
        )
    return decision


def _contains_unordered_collection(
    value: object, *, seen: set[int] | None = None
) -> bool:
    if isinstance(value, Set) and not isinstance(value, str | bytes):
        return True
    if isinstance(value, str | bytes):
        return False
    mapping = (
        cast(Mapping[object, object], value) if isinstance(value, Mapping) else None
    )
    sequence = (
        cast(Sequence[object], value)
        if mapping is None and isinstance(value, Sequence)
        else None
    )
    if mapping is None and sequence is None:
        return False
    active: set[int] = set() if seen is None else seen
    identity = id(_runtime_object(value))
    if identity in active:
        return False
    active.add(identity)
    try:
        if mapping is not None:
            return any(
                _contains_unordered_collection(item, seen=active)
                for pair in mapping.items()
                for item in pair
            )
        assert sequence is not None
        return any(
            _contains_unordered_collection(item, seen=active) for item in sequence
        )
    finally:
        active.remove(identity)


def _runtime_object(value: Any) -> object:
    """Widen adapter-owned fields so deliberate runtime checks stay effective."""
    return value


def _build_adapter(annotation: Any, *, label: str) -> TypeAdapter[Any]:
    try:
        adapter = TypeAdapter(annotation)
        adapter.json_schema(mode="serialization")
        return adapter
    except Exception as exc:
        type_name = getattr(annotation, "__name__", repr(annotation))
        raise ConfigurationError(
            f"{label}: Pydantic cannot use type {type_name}: {exc}"
        ) from exc


def _validate_identity(
    *,
    namespace: object,
    scope: object,
    key: object,
    fingerprint: object,
) -> None:
    if not isinstance(namespace, str) or _NAMESPACE.fullmatch(namespace) is None:
        raise ConfigurationError(
            "run_idempotently: namespace must use dotted snake_case, "
            f"such as 'tasks.create'; got {namespace!r}"
        )
    for name, value in (
        ("scope", scope),
        ("key", key),
        ("fingerprint", fingerprint),
    ):
        if not isinstance(value, str) or not value:
            raise ConfigurationError(
                f"run_idempotently: {name} must be a non-empty string"
            )


def _validated_ttl(
    value: object,
    *,
    label: str,
    optional: bool,
) -> float | None:
    if value is None and optional:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        expectation = "None or " if optional else ""
        raise ConfigurationError(
            f"{label} must be {expectation}a finite number greater than zero, "
            f"got {value!r}"
        )
    try:
        ttl = float(value)
    except OverflowError as exc:
        raise ConfigurationError(
            f"{label} must be a finite number greater than zero"
        ) from exc
    if not isfinite(ttl) or ttl <= 0:
        expectation = "None or " if optional else ""
        raise ConfigurationError(
            f"{label} must be {expectation}a finite number greater than zero, "
            f"got {value!r}"
        )
    return ttl


__all__ = [
    "IDEMPOTENCY_CONFLICT",
    "IDEMPOTENCY_IN_PROGRESS",
    "IdempotencyConflict",
    "IdempotencyDecision",
    "IdempotencyInProgress",
    "IdempotencyReplay",
    "IdempotencyReservation",
    "IdempotencyResultError",
    "IdempotencyStore",
    "IdempotencyStoreError",
    "fingerprint",
    "run_idempotently",
]
