"""Background indexing composition."""

from tenchi.jobs import create_job_dispatcher, job_group, job_handler

from app.features.knowledge.jobs import index_source_job
from app.features.knowledge.use_cases.index_source import index_source
from app.server.observability import use_case_observers

jobs = job_group(job_handler(index_source_job, index_source))

dispatcher = create_job_dispatcher(
    jobs=jobs,
    use_case_observers=use_case_observers,
)
