from tenchi.tasks import task, task_group

from .use_cases.repair_project_members import repair_project_members

repair_project_members_task = task(
    "projects.repair_members",
    repair_project_members,
    description="Replace malformed project member lists with an empty list.",
)

tasks = task_group(repair_project_members_task)
