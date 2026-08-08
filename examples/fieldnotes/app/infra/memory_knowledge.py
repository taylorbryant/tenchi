"""Deterministic in-memory adapters for direct use-case tests."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import uuid4

from pydantic import HttpUrl

from app.features.knowledge.policy import rank_texts
from app.features.knowledge.schemas import (
    Passage,
    PassageMatch,
    Source,
    SourceStatus,
    StoredSource,
)
from app.shared.users import OwnerScope


class MemorySourceRepository:
    def __init__(self) -> None:
        self.items: dict[str, StoredSource] = {}

    async def create(
        self,
        *,
        title: str,
        content: str,
        url: str | None,
        owner: OwnerScope,
    ) -> Source:
        stored = StoredSource(
            id=uuid4().hex,
            owner_id=owner.owner_id,
            title=title,
            content=content,
            url=HttpUrl(url) if url is not None else None,
            status=SourceStatus.PENDING,
        )
        self.items[stored.id] = stored
        return _public_source(stored)

    async def list_owned_by(self, owner: OwnerScope) -> list[Source]:
        return [
            _public_source(source)
            for source in self.items.values()
            if source.owner_id == owner.owner_id
        ]

    async def get_for_indexing(self, source_id: str) -> StoredSource | None:
        return self.items.get(source_id)

    async def mark_indexed(self, source_id: str) -> None:
        source = self.items[source_id]
        self.items[source_id] = source.model_copy(
            update={"status": SourceStatus.INDEXED}
        )

    async def list_all(self) -> list[Source]:
        return [_public_source(source) for source in self.items.values()]


class MemoryPassageIndex:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, Passage]] = {}

    async def replace_source(
        self,
        source: StoredSource,
        passages: Sequence[Passage],
    ) -> None:
        self.items = {
            key: item
            for key, item in self.items.items()
            if item[1].source_id != source.id
        }
        self.items.update(
            {passage.id: (source.owner_id, passage) for passage in passages}
        )

    async def search(
        self,
        query: str,
        *,
        owner: OwnerScope,
        limit: int,
    ) -> list[PassageMatch]:
        visible = {
            passage_id: passage
            for passage_id, (owner_id, passage) in self.items.items()
            if owner_id == owner.owner_id
        }
        ranked = rank_texts(
            query,
            (
                (passage_id, f"{passage.title} {passage.text}")
                for passage_id, passage in visible.items()
            ),
        )
        return [
            PassageMatch.model_validate(
                visible[item.id].model_dump() | {"score": item.score}
            )
            for item in ranked[:limit]
        ]


@dataclass(frozen=True, slots=True)
class MemoryOutboxEntry:
    job: str
    payload_json: bytes


class MemoryOutbox:
    def __init__(self) -> None:
        self.entries: list[MemoryOutboxEntry] = []

    async def enqueue(self, *, job: str, payload_json: bytes) -> None:
        self.entries.append(MemoryOutboxEntry(job=job, payload_json=payload_json))


def _public_source(source: StoredSource) -> Source:
    return Source(
        id=source.id,
        title=source.title,
        url=source.url,
        status=source.status,
    )
