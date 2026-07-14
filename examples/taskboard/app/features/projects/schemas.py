from pydantic import BaseModel, Field


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class GetProjectParams(BaseModel):
    project_id: str


class Project(BaseModel):
    id: str
    name: str
    owner_id: str
