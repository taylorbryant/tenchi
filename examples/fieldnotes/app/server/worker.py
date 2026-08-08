"""Small SQLite outbox worker for background source indexing."""

import asyncio
import logging
import os

import aiosqlite
from pydantic import ValidationError
from tenchi.errors import AppError
from tenchi.jobs import JobNotFoundError, JobResultError

from app.infra.deterministic_answer_generator import DeterministicAnswerGenerator
from app.infra.port_wiring import ensure_schema
from app.infra.sqlite_knowledge import (
    SqliteOutbox,
    SqlitePassageIndex,
    SqliteSourceRepository,
    configure_connection,
)
from app.server.context import AppContext
from app.server.jobs import dispatcher

logger = logging.getLogger("fieldnotes.worker")
POLL_INTERVAL_SECONDS = 1.0


async def process_next(database_path: str) -> bool:
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        sources = SqliteSourceRepository(connection)
        passages = SqlitePassageIndex(connection)
        outbox = SqliteOutbox(connection)
        entry = await outbox.claim_next()
        if entry is None:
            return False
        context = AppContext(
            sources=sources,
            passages=passages,
            answer_generator=DeterministicAnswerGenerator(),
            outbox=outbox,
        )
        try:
            await dispatcher.dispatch(
                entry.job,
                payload_json=entry.payload_json,
                context=context,
            )
        except (ValidationError, JobNotFoundError, JobResultError, AppError) as error:
            await connection.rollback()
            await outbox.mark_failed(entry.id, error=_failure_text(error))
        except BaseException:
            await connection.rollback()
            raise
        else:
            await outbox.mark_delivered(entry.id)
        await connection.commit()
        return True


def _failure_text(error: Exception) -> str:
    if isinstance(error, AppError):
        return f"{error.code}: {error}"
    if isinstance(error, ValidationError):
        return "JOB_INPUT_INVALID: payload does not match the declaration"
    if isinstance(error, JobResultError):
        return "JOB_RESULT_INVALID: result does not match the declaration"
    if isinstance(error, JobNotFoundError):
        return f"JOB_NOT_FOUND: {error}"
    return type(error).__name__


async def drain(database_path: str) -> int:
    settled = 0
    while await process_next(database_path):
        settled += 1
    return settled


async def main() -> None:
    database_path = os.environ.get("FIELDNOTES_DATABASE", "fieldnotes.db")
    await ensure_schema(database_path)
    while True:
        try:
            busy = await process_next(database_path)
        except Exception:
            logger.exception("indexing job failed; retrying after backoff")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        if not busy:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
