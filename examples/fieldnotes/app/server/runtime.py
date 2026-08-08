"""Application resources shared by every Fieldnotes entrypoint."""

import os
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from app.infra.port_wiring import ensure_schema, open_request_ports
from app.server.context import AppContext

DATABASE_PATH = os.environ.get("FIELDNOTES_DATABASE", "fieldnotes.db")


def create_lifespan(
    database_path: str,
) -> Callable[[], AbstractAsyncContextManager[str]]:
    @asynccontextmanager
    async def lifespan() -> AsyncGenerator[str]:
        await ensure_schema(database_path)
        yield database_path

    return lifespan


@asynccontextmanager
async def create_context(database_path: str) -> AsyncGenerator[AppContext]:
    async with open_request_ports(database_path) as ports:
        yield AppContext(
            sources=ports.sources,
            passages=ports.passages,
            answer_generator=ports.answer_generator,
            outbox=ports.outbox,
        )
