from pydantic import BaseModel, Field


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class GetProjectParams(BaseModel):
    project_id: str


class AddProjectMember(BaseModel):
    user_id: str


class MemberAdded(BaseModel):
    """Payload of the ``member_added`` outbox job.

    Enqueuer (``add_project_member``) and worker share this one
    declaration; the worker validates inbound payloads against it before
    any use case runs, mirroring HTTP boundary validation. The payload is
    self-contained — it carries the facts as they were when the event
    happened, so delivery does not re-read state that may have changed
    (or vanished) since enqueue time.
    """

    project_id: str
    project_name: str
    user_id: str


class Project(BaseModel):
    id: str
    name: str
    owner_id: str
    member_ids: tuple[str, ...] = ()
