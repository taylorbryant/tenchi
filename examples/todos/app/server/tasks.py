"""Operational task composition."""

from app.server.runtime import DATABASE_PATH, create_context, create_lifespan
from tenchi.tasks import create_task_runner, task_group

# Add feature task groups here when the application needs operational work.
tasks = task_group()

runner = create_task_runner(
    tasks=tasks,
    context_factory=create_context,
    lifespan=create_lifespan(DATABASE_PATH),
)
