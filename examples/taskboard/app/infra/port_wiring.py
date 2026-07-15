"""Wires app ports to concrete adapters for the selected runtime.

This is the one file that knows the taskboard talks to two database
connections per request: a primary (writes and strong reads, wrapped in
the request transaction) and a read-only connection serving the
staleness-tolerant ``TaskSearch`` port. With SQLite the "replica" is a
second connection to the same file locked into query-only mode; against
Postgres it would be a connection from a replica pool — the wiring
shape is identical (``docs/read-replicas.md`` in the tenchi repository).
"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import aiosqlite

from app.features.projects.ports import NotificationLog, Outbox, ProjectRepository
from app.features.tasks.ports import TaskRepository, TaskSearch

from .sqlite_repositories import (
    SCHEMA,
    SqliteNotificationLog,
    SqliteOutbox,
    SqliteProjectRepository,
    SqliteTaskRepository,
    SqliteTaskSearch,
)


@dataclass(frozen=True, slots=True)
class AppPorts:
    """The concrete ports a request scope hands to the context factory."""

    projects: ProjectRepository
    tasks: TaskRepository
    task_search: TaskSearch
    outbox: Outbox
    notifications: NotificationLog


async def configure_connection(connection: aiosqlite.Connection) -> None:
    """Per-connection pragmas every consumer needs: enforce the declared
    foreign keys (SQLite ignores them by default) and wait for locks
    instead of failing immediately when the server and the outbox worker
    write concurrently."""
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA busy_timeout = 5000")


@asynccontextmanager
async def open_read_connection(
    database_path: str,
) -> AsyncGenerator[aiosqlite.Connection]:
    """The read side: a connection that cannot write.

    ``query_only`` makes SQLite reject every write on this connection,
    so a wiring mistake (a write-path adapter handed the read
    connection) fails loudly instead of silently writing to what should
    be a replica. In production this is where a replica DSN would go.
    """
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        await connection.execute("PRAGMA query_only = ON")
        yield connection


async def ensure_schema(database_path: str) -> None:
    """Create tables once at startup (called from the app lifespan)."""
    async with aiosqlite.connect(database_path) as connection:
        # WAL is persistent per database file; readers stop blocking the
        # writer, which matters once the worker runs beside the server.
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.executescript(SCHEMA)
        await connection.commit()


@asynccontextmanager
async def open_request_ports(database_path: str) -> AsyncGenerator[AppPorts]:
    """One request's unit of work plus its read side.

    The primary connection carries the request transaction — commit on
    success, roll back on error. The read connection is autocommit and
    read-only; it participates in no transaction, exactly as a replica
    would not. AsyncExitStack keeps the acquisitions flat and unwinds
    them in reverse on exit or error.
    """
    async with AsyncExitStack() as stack:
        primary = await stack.enter_async_context(aiosqlite.connect(database_path))
        await configure_connection(primary)
        reader = await stack.enter_async_context(open_read_connection(database_path))
        yield AppPorts(
            projects=SqliteProjectRepository(primary),
            tasks=SqliteTaskRepository(primary),
            task_search=SqliteTaskSearch(reader),
            outbox=SqliteOutbox(primary),
            notifications=SqliteNotificationLog(primary),
        )
        await primary.commit()
