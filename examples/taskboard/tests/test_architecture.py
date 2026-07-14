"""The taskboard obeys the dependency direction tenchi doctor enforces."""

from pathlib import Path

from tenchi.doctor import run_doctor


def test_doctor_finds_no_problems() -> None:
    assert run_doctor(Path(__file__).parent.parent) == []
