"""Authorization rules for projects.

An ability lives in the feature that owns the subject it inspects, so task
use cases import these rules instead of re-deriving project ownership.
Policies take their subjects as arguments and raise; use cases fetch, then
ask.
"""

from app.shared.errors import forbidden, project_not_found
from app.shared.users import User
from tenchi.errors import AppError

from .schemas import Project


def can_view_project(user: User, project: Project | None) -> bool:
    """Owners and members may view; everyone else sees nothing."""
    if project is None:
        return False
    return project.owner_id == user.id or user.id in project.member_ids


def ensure_can_view_project(
    user: User, project: Project | None, *, project_id: str
) -> Project:
    """Missing and unviewable projects look identical, so ids cannot be probed."""
    if project is None or not can_view_project(user, project):
        raise AppError(project_not_found, details={"project_id": project_id})
    return project


def ensure_can_write_project(
    user: User, project: Project | None, *, project_id: str
) -> Project:
    """Writing into a project that exists but is not yours is forbidden."""
    if project is None:
        raise AppError(project_not_found, details={"project_id": project_id})
    if project.owner_id != user.id:
        raise AppError(forbidden, details={"project_id": project_id})
    return project
