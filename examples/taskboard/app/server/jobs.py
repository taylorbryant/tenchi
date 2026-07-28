"""Background-job composition for the outbox worker."""

from app.features.projects.jobs import member_added_job
from app.features.projects.use_cases.notify_member_added import notify_member_added
from app.server.observability import use_case_observers
from tenchi.jobs import create_job_dispatcher, job_group, job_handler

jobs = job_group(job_handler(member_added_job, notify_member_added))

dispatcher = create_job_dispatcher(
    jobs=jobs,
    use_case_observers=use_case_observers,
)
