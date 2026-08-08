"""Read-only deployment observations for Fieldnotes dependencies."""

from tenchi.preflight import PreflightGroup, preflight_check, preflight_group

from app.infra.port_wiring import open_preflight_connection
from app.server.runtime import DATABASE_PATH

_REQUIRED_TABLES = {"sources", "passages", "outbox"}


def build_preflight(database_path: str = DATABASE_PATH) -> PreflightGroup:
    async def database_connectivity() -> None:
        async with open_preflight_connection(database_path) as connection:
            await connection.execute("SELECT 1")

    async def database_schema() -> None:
        async with open_preflight_connection(database_path) as connection:
            cursor = await connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = {str(row[0]) for row in await cursor.fetchall()}
            if not tables >= _REQUIRED_TABLES:
                raise RuntimeError("database schema is incomplete")

    return preflight_group(
        preflight_check(
            "database.connectivity",
            database_connectivity,
            description="Open the configured knowledge database read-only.",
            failure_code="DATABASE_UNAVAILABLE",
        ),
        preflight_check(
            "database.schema",
            database_schema,
            description="Verify the source, passage, and outbox tables.",
            failure_code="DATABASE_SCHEMA_MISMATCH",
        ),
    )


checks = build_preflight()
