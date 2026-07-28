from app.features.projects.schemas import RepairProjectMembersInput
from app.features.projects.use_cases.repair_project_members import (
    repair_project_members,
)
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.server.context import AppContext
from tenchi.idempotency import MemoryIdempotencyStore
from tenchi.rate_limits import MemoryRateLimitStore


async def test_repair_project_members_supports_a_safe_dry_run() -> None:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    context = AppContext(
        projects=projects,
        tasks=tasks,
        task_search=MemoryTaskSearch(projects, tasks),
        idempotency=MemoryIdempotencyStore(),
        rate_limits=MemoryRateLimitStore(),
        outbox=MemoryOutbox(),
        notifications=MemoryNotificationLog(),
    )

    result = await repair_project_members(
        RepairProjectMembersInput(),
        context,
    )

    assert result.scanned == 0
    assert result.repaired == 0
    assert result.dry_run is True
