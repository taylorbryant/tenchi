"""Shared-kernel identity concepts used across features."""

from typing import Protocol

from pydantic import BaseModel

from tenchi.errors import AppError

from .errors import unauthorized


class User(BaseModel):
    id: str
    name: str


class TokenDirectory(Protocol):
    """Resolves a bearer token to a user, or ``None`` when unknown."""

    async def lookup(self, token: str) -> User | None: ...


def require_user(user: User | None) -> User:
    """Assert an authenticated user inside a use case.

    The HTTP hook authenticates; use cases still assert identity for rules
    that matter outside HTTP (direct calls, tests, future workers).
    """
    if user is None:
        raise AppError(unauthorized)
    return user
