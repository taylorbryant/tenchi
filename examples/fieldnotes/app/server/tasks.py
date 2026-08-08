"""Operational reindexing composition."""

from tenchi.tasks import create_task_runner, task_group

from app.features.knowledge.tasks import tasks as knowledge_tasks
from app.server.observability import use_case_observers
from app.server.runtime import DATABASE_PATH, create_context, create_lifespan

tasks = task_group(knowledge_tasks)

runner = create_task_runner(
    tasks=tasks,
    context_factory=create_context,
    lifespan=create_lifespan(DATABASE_PATH),
    use_case_observers=use_case_observers,
)
