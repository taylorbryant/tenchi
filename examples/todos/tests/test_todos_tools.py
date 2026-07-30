"""The example's application-tool manifest stays reproducible."""

from pathlib import Path

from tenchi.cli import main

SNAPSHOT = Path(__file__).parent.parent / "tools.json"


def test_tool_snapshot_is_current() -> None:
    assert main(["tools", "--check", str(SNAPSHOT)]) == 0
