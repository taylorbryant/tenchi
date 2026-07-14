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
    """The concrete ports the lifespan hands to the context factory."""

    projects: ProjectRepository
    tasks: TaskRepository


@asynccontextmanager
async def open_ports(database_path: str) -> AsyncGenerator[AppPorts]:
    """Open the shared connection, ensure the schema, and close on exit."""
    async with aiosqlite.connect(database_path) as connection:
        await connection.executescript(SCHEMA)
        await connection.commit()
        yield AppPorts(
            projects=SqliteProjectRepository(connection),
            tasks=SqliteTaskRepository(connection),
        )
