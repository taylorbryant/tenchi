"""Stable background-job messages owned by the projects feature."""

from tenchi.jobs import job

from .schemas import MemberAdded

member_added_job = job(
    "projects.member_added",
    request=MemberAdded,
    result=None,
    description="Notify a user after project membership is committed.",
)
