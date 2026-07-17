from typing import Protocol

from app.shared.users import OwnerScope

from .schemas import Task, TaskStatus


class TaskRepository(Protocol):
    """Writes and strong reads: always the primary database."""

    async def create(self, *, project_id: str, title: str) -> Task: ...

    async def create_idempotent(
        self,
        *,
        project_id: str,
        title: str,
        owner: OwnerScope,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Task | None:
        """Create once and replay the original result for matching retries.

        Return ``None`` when the owner already used the key for different
        validated input. The claim, task, and response must commit atomically.
        """
        ...

    async def get(self, task_id: str) -> Task | None: ...

    async def save(self, task: Task, *, expected_version: int) -> Task | None:
        """Atomically save and increment ``task`` when its stored version
        equals ``expected_version``; return ``None`` on a stale write."""
        ...


class TaskSearch(Protocol):
    """Staleness-tolerant listing: may be served by a read replica.

    Splitting this from :class:`TaskRepository` names the consistency
    requirement explicitly. Callers that need read-your-writes semantics
    must use ``TaskRepository`` for those reads.
    """

    async def search(
        self,
        *,
        viewer: OwnerScope,
        project_id: str | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        """One page of tasks visible to the viewer — tasks in projects the
        viewer owns or is a member of — plus the total match count. Listing
        must agree with ``get``: anything fetchable is listable."""
        ...
