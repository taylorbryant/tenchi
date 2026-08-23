"""Payload-safe projections of Pydantic validation failures."""

from __future__ import annotations

from typing import get_args

from pydantic import ValidationError
from pydantic_core.core_schema import ErrorType

_PYDANTIC_ERROR_TYPES = frozenset(get_args(ErrorType))


def payload_safe_validation_issues(
    error: ValidationError,
) -> tuple[dict[str, str], ...]:
    """Return stable issue kinds without input values, paths, or messages."""
    issues: list[dict[str, str]] = []
    for raw in error.errors(include_input=False, include_url=False):
        issue_type = raw["type"]
        issues.append(
            {
                "type": (
                    issue_type
                    if issue_type in _PYDANTIC_ERROR_TYPES
                    else "validation_error"
                )
            }
        )
    return tuple(issues)
