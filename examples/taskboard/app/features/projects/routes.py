from tenchi.responses import PresentedResponse, present
from tenchi.routes import route, route_group

from .contracts import (
    CreatedProjectHeaders,
    add_project_member_contract,
    already_a_member,
    create_project_contract,
    get_project_contract,
    list_projects_contract,
    member_added,
)
from .schemas import Project
from .use_cases.add_project_member import AddProjectMemberResult, add_project_member
from .use_cases.create_project import create_project
from .use_cases.get_project import get_project
from .use_cases.list_projects import list_projects


def create_project_headers(project: Project) -> CreatedProjectHeaders:
    return CreatedProjectHeaders(Location=f"/projects/{project.id}")


def present_add_project_member(result: AddProjectMemberResult) -> PresentedResponse:
    outcome = member_added if result.added else already_a_member
    return present(outcome, body=result.project)


routes = route_group(
    route(
        create_project_contract,
        create_project,
        response_headers=create_project_headers,
    ),
    route(get_project_contract, get_project),
    route(list_projects_contract, list_projects),
    route(
        add_project_member_contract,
        add_project_member,
        present=present_add_project_member,
    ),
)
