"""The outbox worker: asgi.py's sibling entrypoint (docs/events.md).

Run alongside the HTTP server with:

    uv run python -m app.server.worker

Each job is one unit of work on its own connection: atomically claim the
oldest pending outbox row (the claim is safe with several workers — see
``SqliteOutbox``), validate its payload against the job's declared
model — the same boundary discipline as HTTP; undeclared jobs and
malformed payloads are dead-lettered, never retried and never allowed
near a use case — then run an ordinary use case. Everything commits
together; a crash mid-job rolls the claim back and the job is claimed
again on a later pass (the loop survives job failures, backing off so a
deterministically failing job cannot hot-spin the process).
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
from pydantic import TypeAdapter, ValidationError

from app.features.projects.schemas import MemberAdded
from app.features.projects.use_cases.notify_member_added import notify_member_added
from app.infra.port_wiring import configure_connection, ensure_schema
from app.infra.sqlite_repositories import (
    SqliteNotificationLog,
    SqliteOutbox,
    SqliteProjectRepository,
    SqliteTaskRepository,
)
from app.server.context import AppContext

logger = logging.getLogger("taskboard.worker")

JobHandler = tuple[type[Any], Callable[[Any, AppContext], Awaitable[None]]]

JOB_HANDLERS: dict[str, JobHandler] = {
    "member_added": (MemberAdded, notify_member_added),
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

        handler = JOB_HANDLERS.get(entry.job)
        if handler is None:
            await outbox.mark_failed(entry.id, error=f"unknown job {entry.job!r}")
        else:
            payload_type, use_case = handler
            try:
                request = TypeAdapter(payload_type).validate_json(entry.payload)
            except ValidationError as error:
                await outbox.mark_failed(entry.id, error=str(error))
            else:
                context = AppContext(
                    projects=SqliteProjectRepository(connection),
                    tasks=SqliteTaskRepository(connection),
                    outbox=outbox,
                    notifications=SqliteNotificationLog(connection),
                )
                await use_case(request, context)

        await connection.commit()
        return True


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
