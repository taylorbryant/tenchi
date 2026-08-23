"""Reusable state-machine checks for application-owned idempotency stores."""

from __future__ import annotations

import asyncio
from asyncio import CancelledError
from typing import Any
from uuid import uuid4

from ._store_conformance import (
    CaseFailure,
    ClockAdvance,
    IdempotencyStoreFactory,
    advance_clock,
    require,
    require_callable,
    require_type,
    run_case,
)
from .idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyReplay,
    IdempotencyReservation,
)


async def verify_idempotency_store(
    open_store: IdempotencyStoreFactory,
    *,
    advance: ClockAdvance,
) -> None:
    """Verify the atomic state machine required by ``IdempotencyStore``."""
    require_callable(open_store, label="verify_idempotency_store: open_store")
    require_callable(advance, label="verify_idempotency_store: advance")
    run = uuid4().hex
    next_case = 0

    def identity(case: str) -> tuple[str, str, str]:
        nonlocal next_case
        next_case += 1
        return (
            f"conformance.{case}_{run}",
            f"scope-{next_case}",
            f"key-{next_case}",
        )

    async def reservation_and_replay() -> None:
        namespace, scope, key = identity("lifecycle")
        reservation = require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="input-a",
                completed_ttl=None,
            ),
            IdempotencyReservation,
            "the first reserve must return IdempotencyReservation",
        )
        require(
            (
                reservation.namespace,
                reservation.scope,
                reservation.key,
                reservation.fingerprint,
                reservation.completed_ttl,
            )
            == (namespace, scope, key, "input-a", None),
            "the reservation must preserve its requested identity and options",
        )
        token = _runtime_object(reservation.token)
        require(
            isinstance(token, str) and bool(token),
            "the reservation token must be a non-empty string",
        )

        active = require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="input-a",
                completed_ttl=None,
            ),
            IdempotencyInProgress,
            "a matching active reservation must report IdempotencyInProgress",
        )
        retry_after = _runtime_object(active.retry_after_seconds)
        require(
            retry_after is None
            or (
                isinstance(retry_after, int)
                and not isinstance(retry_after, bool)
                and retry_after > 0
            ),
            "retry_after_seconds must be a positive integer or None",
        )
        require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="input-b",
                completed_ttl=None,
            ),
            IdempotencyConflict,
            "a reused key with another fingerprint must report conflict",
        )

        await _complete(open_store, reservation, result_json=b'{"ok":true}')
        await _expect_complete_failure(
            open_store,
            reservation,
            result_json=b'{"changed":true}',
        )
        replay = require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="input-a",
                completed_ttl=None,
            ),
            IdempotencyReplay,
            "a completed matching operation must return its replay",
        )
        require(
            replay.result_json == b'{"ok":true}',
            "the replay must preserve the exact serialized result",
        )
        require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="input-b",
                completed_ttl=None,
            ),
            IdempotencyConflict,
            "a completed key with another fingerprint must report conflict",
        )
        await _abandon(open_store, reservation)
        require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="input-a",
                completed_ttl=None,
            ),
            IdempotencyReplay,
            "abandoning a completed reservation must not remove its replay",
        )

    async def identity_isolation() -> None:
        namespace, scope, key = identity("identity")
        identities = (
            (namespace, scope, key),
            (f"{namespace}.other", scope, key),
            (namespace, f"{scope}-other", key),
            (namespace, scope, f"{key}-other"),
        )
        decisions = [
            await _reserve(
                open_store,
                namespace=item_namespace,
                scope=item_scope,
                key=item_key,
                fingerprint="same-input",
                completed_ttl=None,
            )
            for item_namespace, item_scope, item_key in identities
        ]
        require(
            all(isinstance(item, IdempotencyReservation) for item in decisions),
            "namespace, scope, and key must each isolate reservations",
        )

    async def abandon_is_token_fenced() -> None:
        namespace, scope, key = identity("abandon")
        first = await _required_reservation(
            open_store,
            namespace=namespace,
            scope=scope,
            key=key,
            fingerprint="same-input",
            completed_ttl=None,
        )
        await _abandon(open_store, first)
        await _abandon(open_store, first)
        second = await _required_reservation(
            open_store,
            namespace=namespace,
            scope=scope,
            key=key,
            fingerprint="same-input",
            completed_ttl=None,
        )
        require(
            second.token != first.token,
            "a new reservation after abandon must receive a new token",
        )
        await _abandon(open_store, first)
        require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="same-input",
                completed_ttl=None,
            ),
            IdempotencyInProgress,
            "an old token must not abandon the current reservation",
        )

    async def expiration_is_token_fenced() -> None:
        namespace, scope, key = identity("reservation_expiration")
        first = await _required_reservation(
            open_store,
            namespace=namespace,
            scope=scope,
            key=key,
            fingerprint="same-input",
            completed_ttl=None,
            reservation_ttl=10,
        )
        await advance_clock(advance, 9.9)
        require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="same-input",
                completed_ttl=None,
                reservation_ttl=10,
            ),
            IdempotencyInProgress,
            "a reservation must remain active before its TTL boundary",
        )
        await advance_clock(advance, 0.1)
        second = await _required_reservation(
            open_store,
            namespace=namespace,
            scope=scope,
            key=key,
            fingerprint="same-input",
            completed_ttl=None,
            reservation_ttl=10,
        )
        require(
            second.token != first.token,
            "an expired reservation must be replaced with a new token",
        )
        await _expect_complete_failure(
            open_store,
            first,
            result_json=b'{"stale":true}',
        )
        await _abandon(open_store, first)
        require_type(
            await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="same-input",
                completed_ttl=None,
            ),
            IdempotencyInProgress,
            "expired tokens must not complete or abandon the new reservation",
        )
        await _complete(open_store, second, result_json=b'{"fresh":true}')

    async def completed_expiration() -> None:
        expiring_namespace, expiring_scope, expiring_key = identity(
            "completed_expiration"
        )
        permanent_namespace, permanent_scope, permanent_key = identity(
            "completed_retention"
        )
        expiring = await _required_reservation(
            open_store,
            namespace=expiring_namespace,
            scope=expiring_scope,
            key=expiring_key,
            fingerprint="expiring",
            completed_ttl=10,
        )
        permanent = await _required_reservation(
            open_store,
            namespace=permanent_namespace,
            scope=permanent_scope,
            key=permanent_key,
            fingerprint="permanent",
            completed_ttl=None,
        )
        await _complete(open_store, expiring, result_json=b'"expiring"')
        await _complete(open_store, permanent, result_json=b'"permanent"')
        await advance_clock(advance, 9.9)
        require_type(
            await _reserve(
                open_store,
                namespace=expiring_namespace,
                scope=expiring_scope,
                key=expiring_key,
                fingerprint="expiring",
                completed_ttl=10,
            ),
            IdempotencyReplay,
            "a completed record must remain replayable before its TTL boundary",
        )
        await advance_clock(advance, 0.1)
        require_type(
            await _reserve(
                open_store,
                namespace=expiring_namespace,
                scope=expiring_scope,
                key=expiring_key,
                fingerprint="expiring",
                completed_ttl=10,
            ),
            IdempotencyReservation,
            "a completed record must expire at its completed TTL boundary",
        )
        require_type(
            await _reserve(
                open_store,
                namespace=permanent_namespace,
                scope=permanent_scope,
                key=permanent_key,
                fingerprint="permanent",
                completed_ttl=None,
            ),
            IdempotencyReplay,
            "completed_ttl=None must retain the replay after clock advancement",
        )

    async def concurrent_reservation() -> None:
        namespace, scope, key = identity("concurrency")

        async def reserve_once() -> object:
            return await _reserve(
                open_store,
                namespace=namespace,
                scope=scope,
                key=key,
                fingerprint="same-input",
                completed_ttl=None,
            )

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(reserve_once()) for _ in range(12)]
        decisions = [task.result() for task in tasks]
        winners = [
            item for item in decisions if isinstance(item, IdempotencyReservation)
        ]
        waiting = [
            item for item in decisions if isinstance(item, IdempotencyInProgress)
        ]
        require(
            len(winners) == 1 and len(waiting) == 11,
            "concurrent matching reservations must have exactly one winner",
        )

    cases = (
        ("reservation_and_replay", reservation_and_replay),
        ("identity_isolation", identity_isolation),
        ("abandon_is_token_fenced", abandon_is_token_fenced),
        ("expiration_is_token_fenced", expiration_is_token_fenced),
        ("completed_expiration", completed_expiration),
        ("concurrent_reservation", concurrent_reservation),
    )
    for name, operation in cases:
        await run_case("IdempotencyStore", name, operation)


async def _reserve(
    open_store: IdempotencyStoreFactory,
    *,
    namespace: str,
    scope: str,
    key: str,
    fingerprint: str,
    completed_ttl: float | None,
    reservation_ttl: float = 10,
) -> object:
    async with open_store() as store:
        return await store.reserve(
            namespace=namespace,
            scope=scope,
            key=key,
            fingerprint=fingerprint,
            completed_ttl=completed_ttl,
            reservation_ttl=reservation_ttl,
        )


async def _required_reservation(
    open_store: IdempotencyStoreFactory,
    *,
    namespace: str,
    scope: str,
    key: str,
    fingerprint: str,
    completed_ttl: float | None,
    reservation_ttl: float = 10,
) -> IdempotencyReservation:
    decision = await _reserve(
        open_store,
        namespace=namespace,
        scope=scope,
        key=key,
        fingerprint=fingerprint,
        completed_ttl=completed_ttl,
        reservation_ttl=reservation_ttl,
    )
    return require_type(
        decision,
        IdempotencyReservation,
        f"expected a reservation, got {type(decision).__name__}",
    )


async def _complete(
    open_store: IdempotencyStoreFactory,
    reservation: IdempotencyReservation,
    *,
    result_json: bytes,
) -> None:
    async with open_store() as store:
        await store.complete(reservation, result_json=result_json)


async def _expect_complete_failure(
    open_store: IdempotencyStoreFactory,
    reservation: IdempotencyReservation,
    *,
    result_json: bytes,
) -> None:
    try:
        await _complete(open_store, reservation, result_json=result_json)
    except (CancelledError, KeyboardInterrupt):
        raise
    except Exception:
        return
    raise CaseFailure("completing an expired or replaced token must fail")


async def _abandon(
    open_store: IdempotencyStoreFactory,
    reservation: IdempotencyReservation,
) -> None:
    async with open_store() as store:
        await store.abandon(reservation)


def _runtime_object(value: Any) -> object:
    return value


__all__ = ["verify_idempotency_store"]
