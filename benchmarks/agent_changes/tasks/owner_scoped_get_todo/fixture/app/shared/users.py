from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel
from tenchi.errors import AppError

from .errors import unauthorized


class User(BaseModel):
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class OwnerScope:
    owner_id: str


class TokenDirectory(Protocol):
    async def lookup(self, token: str) -> User | None: ...


def require_user(user: User | None) -> User:
    if user is None:
        raise AppError(unauthorized)
    return user


def require_owner_scope(user: User | None) -> OwnerScope:
    return OwnerScope(owner_id=require_user(user).id)
