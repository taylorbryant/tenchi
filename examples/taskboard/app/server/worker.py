"""The outbox worker: asgi.py's sibling entrypoint (docs/events.md).

Run alongside the HTTP server with:

    uv run python -m app.server.worker

Each job is one unit of work on its own connection: atomically claim the
oldest pending outbox row (the claim is safe with several workers — see
``SqliteOutbox``), then hand the raw payload to ``tenchi.execution``'s
``execute``, which validates it against the use case's own request
annotation — the same boundary validation as HTTP applies.

Every job ends in exactly one of three ways:

- **Delivered** — the use case ran; its writes and the settled row
  commit together.
- **Dead-lettered** — deterministic failures: unknown job names,
  payloads that fail validation, miswired handlers (``ExecutionError``),
  and business rejections (``AppError``). The job's transaction is
  rolled back first, so partial writes never commit, then the row is
  settled with the error preserved. Never retried: retrying a
  deterministic failure would starve every job queued behind it.
- **Retried** — everything else (infrastructure errors: the database is
  locked, a network dependency is down). The transaction rolls back,
  the claim with it, and the row is claimed again on a later pass. The
  loop logs and backs off; note a *persistently* failing dependency
  keeps the queue's head blocked until it recovers — the correct
  behavior for a transient outage, worth alerting on if it lasts.
"""

import asyncio
import logging
import os

import aiosqlite
from pydantic import ValidationError

from app.features.projects.use_cases.notify_member_added import notify_member_added
from app.infra.port_wiring import configure_connection, ensure_schema
from app.infra.sqlite_repositories import (
    SqliteNotificationLog,
    SqliteOutbox,
    SqliteProjectRepository,
    SqliteTaskRepository,
    SqliteTaskSearch,
)
from app.server.context import AppContext
from tenchi.errors import AppError
from tenchi.execution import ExecutionError, execute
from tenchi.routes import UseCase

logger = logging.getLogger("taskboard.worker")

# The use case's request annotation drives payload validation, so the
# registry is just names to functions.
JOB_HANDLERS: dict[str, UseCase] = {
    "member_added": notify_member_added,
}

POLL_INTERVAL_SECONDS = 1.0


async def process_next(database_path: str) -> bool:
    """Process the oldest pending job. Returns False when the outbox is
    empty, True when a row was settled (delivered or dead-lettered)."""
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        outbox = SqliteOutbox(connection)
        entry = await outbox.claim_next()
        if entry is None:
            return False

        use_case = JOB_HANDLERS.get(entry.job)
        if use_case is None:
            await outbox.mark_failed(entry.id, error=f"unknown job {entry.job!r}")
        else:
            context = AppContext(
                projects=SqliteProjectRepository(connection),
                tasks=SqliteTaskRepository(connection),
                task_search=SqliteTaskSearch(connection),
                outbox=outbox,
                notifications=SqliteNotificationLog(connection),
            )
            try:
                await execute(use_case, request_json=entry.payload, context=context)
            except (ValidationError, ExecutionError, AppError) as error:
                # Deterministic failures — bad payload, miswired handler,
                # business rejection — dead-letter instead of retrying;
                # retrying would starve every job behind this one. Roll
                # back first so partial writes never commit alongside
                # the dead-letter record.
                await connection.rollback()
                await outbox.mark_failed(entry.id, error=_failure_text(error))

        await connection.commit()
        return True


def _failure_text(error: Exception) -> str:
    if isinstance(error, AppError):
        return f"{error.code}: {error}"
    return str(error)


async def drain(database_path: str) -> int:
    """Process pending jobs until the outbox is empty; returns the count."""
    settled = 0
    while await process_next(database_path):
        settled += 1
    return settled


async def main() -> None:
    database_path = os.environ.get("TASKBOARD_DATABASE", "taskboard.db")
    await ensure_schema(database_path)
    while True:
        try:
            busy = await process_next(database_path)
        except Exception:
            # The failed job's transaction rolled back, so it will be
            # retried; the worker must outlive individual job failures.
            logger.exception("outbox job failed; retrying after backoff")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        if not busy:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
