from pathlib import Path

from tenchi.cli import main

SNAPSHOT = Path(__file__).parent.parent / "evaluations.json"


def test_evaluation_snapshot_is_current() -> None:
    assert main(["eval", "snapshot", "--check", str(SNAPSHOT)]) == 0
