"""Wires app ports to concrete adapters for the selected runtime."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiosqlite

from app.features.projects.ports import NotificationLog, Outbox, ProjectRepository
from app.features.tasks.ports import TaskRepository

from .sqlite_repositories import (
    SCHEMA,
    SqliteNotificationLog,
    SqliteOutbox,
    SqliteProjectRepository,
    SqliteTaskRepository,
)


@dataclass(frozen=True, slots=True)
class AppPorts:
    """The concrete ports a request scope hands to the context factory."""

    projects: ProjectRepository
    tasks: TaskRepository
    outbox: Outbox
    notifications: NotificationLog


async def configure_connection(connection: aiosqlite.Connection) -> None:
    """Per-connection pragmas every consumer needs: enforce the declared
    foreign keys (SQLite ignores them by default) and wait for locks
    instead of failing immediately when the server and the outbox worker
    write concurrently."""
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA busy_timeout = 5000")


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
    """One request's unit of work: a connection whose transaction commits
    on success; closing without a commit rolls back on error."""
    async with aiosqlite.connect(database_path) as connection:
        await configure_connection(connection)
        yield AppPorts(
            projects=SqliteProjectRepository(connection),
            tasks=SqliteTaskRepository(connection),
            outbox=SqliteOutbox(connection),
            notifications=SqliteNotificationLog(connection),
        )
        await connection.commit()
