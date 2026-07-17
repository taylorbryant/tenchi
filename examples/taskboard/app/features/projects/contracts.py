from pydantic import BaseModel, Field

from app.shared.errors import forbidden, project_not_found
from tenchi.contracts import contract
from tenchi.responses import response

from .schemas import AddProjectMember, CreateProject, GetProjectParams, Project


class CreatedProjectHeaders(BaseModel):
    location: str = Field(alias="Location")


member_added = response(
    Project,
    status=201,
    description="The member was added",
)
already_a_member = response(
    Project,
    status=200,
    description="The user was already a member",
)


create_project_contract = contract(
    method="POST",
    path="/projects",
    request=CreateProject,
    response=Project,
    response_headers=CreatedProjectHeaders,
    status=201,
    summary="Create a project owned by the current user",
    tags=("projects",),
)

get_project_contract = contract(
    method="GET",
    path="/projects/{project_id}",
    params=GetProjectParams,
    response=Project,
    errors=(project_not_found,),
    summary="Get one of the current user's projects",
    tags=("projects",),
)

list_projects_contract = contract(
    method="GET",
    path="/projects",
    response=list[Project],
    summary="List projects owned by the current user",
    tags=("projects",),
)

add_project_member_contract = contract(
    method="POST",
    path="/projects/{project_id}/members",
    params=GetProjectParams,
    request=AddProjectMember,
    responses=(member_added, already_a_member),
    errors=(project_not_found, forbidden),
    summary="Add a member to one of the current user's projects",
    tags=("projects",),
)
