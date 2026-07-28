"""Reusable state-machine checks for application-owned rate-limit stores."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from ._store_conformance import (
    ClockAdvance,
    RateLimitStoreFactory,
    advance_clock,
    require,
    require_callable,
    require_type,
    run_case,
)
from .rate_limits import RateLimitExceeded, RateLimitPermit


async def verify_rate_limit_store(
    open_store: RateLimitStoreFactory,
    *,
    advance: ClockAdvance,
) -> None:
    """Verify the atomic fixed-window behavior required by ``RateLimitStore``."""
    require_callable(open_store, label="verify_rate_limit_store: open_store")
    require_callable(advance, label="verify_rate_limit_store: advance")
    run = uuid4().hex
    next_case = 0

    def identity(case: str) -> tuple[str, str]:
        nonlocal next_case
        next_case += 1
        return f"conformance.{case}_{run}", f"scope-{next_case}"

    async def capacity_and_rejection() -> None:
        namespace, scope = identity("capacity")
        require(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=5,
                window_seconds=10,
                cost=4,
            )
            == RateLimitPermit(5, 1, 10),
            "the first cost must open a full-duration window",
        )
        require(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=5,
                window_seconds=10,
                cost=2,
            )
            == RateLimitExceeded(5, 10),
            "a cost beyond remaining capacity must be rejected",
        )
        require(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=5,
                window_seconds=10,
                cost=1,
            )
            == RateLimitPermit(5, 0, 10),
            "a rejected cost must not consume the remaining capacity",
        )

    async def reset_boundary() -> None:
        namespace, scope = identity("reset")
        require_type(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=1,
                window_seconds=10,
                cost=1,
            ),
            RateLimitPermit,
            "the first cost must be permitted",
        )
        require_type(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=1,
                window_seconds=10,
                cost=1,
            ),
            RateLimitExceeded,
            "an exhausted window must reject another cost",
        )
        await advance_clock(advance, 10)
        require(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=1,
                window_seconds=10,
                cost=1,
            )
            == RateLimitPermit(1, 0, 10),
            "the reset boundary must open a fresh full-duration window",
        )

    async def policy_change_resets_window() -> None:
        namespace, scope = identity("policy")
        await _consume(
            open_store,
            namespace=namespace,
            scope=scope,
            limit=2,
            window_seconds=10,
            cost=1,
        )
        require(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=3,
                window_seconds=10,
                cost=1,
            )
            == RateLimitPermit(3, 2, 10),
            "changing the limit must open a fresh window",
        )
        require(
            await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=3,
                window_seconds=20,
                cost=1,
            )
            == RateLimitPermit(3, 2, 20),
            "changing the duration must open a fresh window",
        )

    async def identity_isolation() -> None:
        namespace, scope = identity("identity")
        decisions = [
            await _consume(
                open_store,
                namespace=item_namespace,
                scope=item_scope,
                limit=1,
                window_seconds=10,
                cost=1,
            )
            for item_namespace, item_scope in (
                (namespace, scope),
                (f"{namespace}.other", scope),
                (namespace, f"{scope}-other"),
            )
        ]
        require(
            decisions
            == [
                RateLimitPermit(1, 0, 10),
                RateLimitPermit(1, 0, 10),
                RateLimitPermit(1, 0, 10),
            ],
            "namespace and scope must each isolate fixed windows",
        )

    async def concurrent_admission() -> None:
        namespace, scope = identity("concurrency")

        async def consume_once() -> object:
            return await _consume(
                open_store,
                namespace=namespace,
                scope=scope,
                limit=5,
                window_seconds=10,
                cost=1,
            )

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(consume_once()) for _ in range(20)]
        decisions = [task.result() for task in tasks]
        permitted = [item for item in decisions if isinstance(item, RateLimitPermit)]
        rejected = [item for item in decisions if isinstance(item, RateLimitExceeded)]
        require(
            len(permitted) == 5 and len(rejected) == 15,
            "twenty concurrent costs against a limit of five must admit five",
        )
        require(
            sorted(item.remaining for item in permitted) == [0, 1, 2, 3, 4],
            "concurrent permits must report each remaining-capacity value once",
        )

    cases = (
        ("capacity_and_rejection", capacity_and_rejection),
        ("reset_boundary", reset_boundary),
        ("policy_change_resets_window", policy_change_resets_window),
        ("identity_isolation", identity_isolation),
        ("concurrent_admission", concurrent_admission),
    )
    for name, operation in cases:
        await run_case("RateLimitStore", name, operation)


async def _consume(
    open_store: RateLimitStoreFactory,
    *,
    namespace: str,
    scope: str,
    limit: int,
    window_seconds: float,
    cost: int,
) -> object:
    async with open_store() as store:
        return await store.consume(
            namespace=namespace,
            scope=scope,
            limit=limit,
            window_seconds=window_seconds,
            cost=cost,
        )


__all__ = ["verify_rate_limit_store"]
