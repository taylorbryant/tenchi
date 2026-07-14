"""Wires app ports to concrete adapters for the selected runtime."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiosqlite

from app.features.projects.ports import ProjectRepository
from app.features.tasks.ports import TaskRepository

from .sqlite_repositories import (
    SCHEMA,
    SqliteProjectRepository,
    SqliteTaskRepository,
)


@dataclass(frozen=True, slots=True)
class AppPorts:
    """The concrete ports a request scope hands to the context factory."""

    projects: ProjectRepository
    tasks: TaskRepository


async def ensure_schema(database_path: str) -> None:
    """Create tables once at startup (called from the app lifespan)."""
    async with aiosqlite.connect(database_path) as connection:
        await connection.executescript(SCHEMA)
        await connection.commit()


@asynccontextmanager
async def open_request_ports(database_path: str) -> AsyncGenerator[AppPorts]:
    """One request's unit of work: a connection whose transaction commits
    on success; closing without a commit rolls back on error."""
    async with aiosqlite.connect(database_path) as connection:
        yield AppPorts(
            projects=SqliteProjectRepository(connection),
            tasks=SqliteTaskRepository(connection),
        )
        await connection.commit()
