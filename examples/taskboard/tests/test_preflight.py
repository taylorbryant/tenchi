import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from app.infra.port_wiring import ensure_schema, open_preflight_connection
from app.server.preflight import build_preflight
from tenchi.preflight import run_preflight


async def test_preflight_observes_connectivity_and_schema_without_writing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.db"

    missing_report = await run_preflight(build_preflight(str(missing)))

    assert not missing_report.ok
    assert {outcome.status for outcome in missing_report.outcomes} == {"failed"}
    assert not missing.exists()

    empty = tmp_path / "empty.db"
    async with aiosqlite.connect(empty):
        pass

    empty_report = await run_preflight(build_preflight(str(empty)))

    assert [outcome.status for outcome in empty_report.outcomes] == [
        "passed",
        "failed",
    ]
    assert empty_report.outcomes[1].failure_code == "DATABASE_SCHEMA_MISMATCH"

    ready = tmp_path / "ready.db"
    await ensure_schema(str(ready))

    async with open_preflight_connection(str(ready)) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            await connection.execute(
                "INSERT INTO projects "
                "(id, name, owner_id, member_ids) VALUES (?, ?, ?, ?)",
                ("write-probe", "Write probe", "operator", "[]"),
            )

    ready_report = await run_preflight(build_preflight(str(ready)))

    assert ready_report.ok
    assert [outcome.name for outcome in ready_report.outcomes] == [
        "database.connectivity",
        "database.schema",
    ]
    assert all(outcome.status == "passed" for outcome in ready_report.outcomes)
