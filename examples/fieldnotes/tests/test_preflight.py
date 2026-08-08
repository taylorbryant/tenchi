import sqlite3
from pathlib import Path

import pytest
from app.infra.port_wiring import ensure_schema, open_preflight_connection
from app.server.preflight import build_preflight
from tenchi.preflight import run_preflight


@pytest.mark.parametrize(
    "filename",
    ("fieldnotes.db", "field notes.db", "field?notes.db", "field#notes.db"),
)
async def test_preflight_is_read_only_and_checks_schema(
    tmp_path: Path,
    filename: str,
) -> None:
    database_path = str(tmp_path / filename)
    await ensure_schema(database_path)

    async with open_preflight_connection(database_path) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            await connection.execute(
                "INSERT INTO sources "
                "(id, owner_id, title, content, status) VALUES (?, ?, ?, ?, ?)",
                ("probe", "operator", "Probe", "Probe", "pending"),
            )

    report = await run_preflight(build_preflight(database_path))

    assert report.ok is True
    assert [item.name for item in report.outcomes] == [
        "database.connectivity",
        "database.schema",
    ]
