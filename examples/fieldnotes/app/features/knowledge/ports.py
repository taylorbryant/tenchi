from collections.abc import Sequence
from typing import Protocol

from app.shared.users import OwnerScope

from .schemas import (
    GeneratedAnswer,
    Passage,
    PassageMatch,
    Source,
    StoredSource,
)


class SourceRepository(Protocol):
    async def create(
        self,
        *,
        title: str,
        content: str,
        url: str | None,
        owner: OwnerScope,
    ) -> Source: ...

    async def list_owned_by(self, owner: OwnerScope) -> list[Source]: ...

    async def get_for_indexing(self, source_id: str) -> StoredSource | None: ...

    async def mark_indexed(self, source_id: str) -> None: ...

    async def list_all(self) -> list[Source]: ...


class PassageIndex(Protocol):
    async def replace_source(
        self,
        source: StoredSource,
        passages: Sequence[Passage],
    ) -> None: ...

    async def search(
        self,
        query: str,
        *,
        owner: OwnerScope,
        limit: int,
    ) -> list[PassageMatch]: ...


class AnswerGenerator(Protocol):
    async def generate(
        self,
        *,
        question: str,
        passages: Sequence[PassageMatch | Passage],
    ) -> GeneratedAnswer: ...


class Outbox(Protocol):
    async def enqueue(self, *, job: str, payload_json: bytes) -> None: ...
