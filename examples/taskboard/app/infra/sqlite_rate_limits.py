"""SQLite fixed-window rate limits sharing the application transaction."""

from collections.abc import Callable
from math import ceil
from time import time

import aiosqlite

from tenchi.rate_limits import (
    RateLimitDecision,
    RateLimitExceeded,
    RateLimitPermit,
)


class SqliteRateLimitStore:
    """Atomic, process-shared logical-operation limits for taskboard.

    The adapter shares the request connection so a task creation and its
    consumed capacity commit or roll back together.
    """

    def __init__(
        self,
        connection: aiosqlite.Connection,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self._connection = connection
        self._clock = clock

    async def consume(
        self,
        *,
        namespace: str,
        scope: str,
        limit: int,
        window_seconds: float,
        cost: int,
    ) -> RateLimitDecision:
        # Acquire SQLite's writer lock before sampling time. Concurrent
        # callers may wait here; sampling first could make a waiter's view of
        # a window appear longer than the configured duration.
        await self._connection.execute("DELETE FROM rate_limit_windows WHERE 0")
        now = self._clock()
        await self._connection.execute(
            "DELETE FROM rate_limit_windows WHERE resets_at <= ?",
            (now,),
        )
        cursor = await self._connection.execute(
            "SELECT limit_count, window_seconds, used, resets_at "
            "FROM rate_limit_windows "
            "WHERE namespace = ? AND scope = ?",
            (namespace, scope),
        )
        row = await cursor.fetchone()
        if row is None or int(row[0]) != limit or float(row[1]) != window_seconds:
            resets_at = now + window_seconds
            await self._connection.execute(
                "INSERT INTO rate_limit_windows "
                "(namespace, scope, limit_count, window_seconds, used, "
                "resets_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (namespace, scope) DO UPDATE SET "
                "limit_count = excluded.limit_count, "
                "window_seconds = excluded.window_seconds, "
                "used = excluded.used, resets_at = excluded.resets_at",
                (
                    namespace,
                    scope,
                    limit,
                    window_seconds,
                    cost,
                    resets_at,
                ),
            )
            return RateLimitPermit(
                limit=limit,
                remaining=limit - cost,
                reset_after_seconds=window_seconds,
            )

        used = int(row[2])
        resets_at = float(row[3])
        reset_after = resets_at - now
        if used + cost > limit:
            return RateLimitExceeded(
                limit=limit,
                retry_after_seconds=max(1, ceil(reset_after)),
            )
        used += cost
        await self._connection.execute(
            "UPDATE rate_limit_windows SET used = ? WHERE namespace = ? AND scope = ?",
            (used, namespace, scope),
        )
        return RateLimitPermit(
            limit=limit,
            remaining=limit - used,
            reset_after_seconds=reset_after,
        )
