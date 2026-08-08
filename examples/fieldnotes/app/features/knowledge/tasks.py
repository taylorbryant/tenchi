from tenchi.tasks import task, task_group

from .use_cases.reindex_sources import reindex_sources

reindex_sources_task = task(
    "knowledge.reindex_sources",
    reindex_sources,
    description="Queue fresh indexing jobs for every saved source.",
)

tasks = task_group(reindex_sources_task)
