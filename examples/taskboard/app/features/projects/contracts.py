from app.shared.errors import project_not_found
from tenchi.contracts import contract

from .schemas import CreateProject, GetProjectParams, Project

create_project_contract = contract(
    method="POST",
    path="/projects",
    request=CreateProject,
    response=Project,
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
