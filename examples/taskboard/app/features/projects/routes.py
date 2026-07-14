from tenchi.routes import route, route_group

from .contracts import (
    create_project_contract,
    get_project_contract,
    list_projects_contract,
)
from .use_cases.create_project import create_project
from .use_cases.get_project import get_project
from .use_cases.list_projects import list_projects

routes = route_group(
    route(create_project_contract, create_project),
    route(get_project_contract, get_project),
    route(list_projects_contract, list_projects),
)
