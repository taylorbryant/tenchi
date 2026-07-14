"""End-to-end flows driven by the typed client, composed like asgi.py.

Two authenticated clients (alice and bob) share one app instance so
ownership rules are exercised across users.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from starlette.applications import Starlette

from app.features.projects.contracts import (
    create_project_contract,
    get_project_contract,
    list_projects_contract,
)
from app.features.projects.schemas import CreateProject, GetProjectParams
from app.features.tasks.contracts import (
    create_task_contract,
    list_tasks_contract,
    update_task_contract,
)
from app.features.tasks.schemas import (
    CreateTask,
    GetTaskParams,
    ListTasksQuery,
    TaskStatus,
    UpdateTask,
)
from app.infra.memory_repositories import (
    MemoryProjectRepository,
    MemoryTaskRepository,
)
from app.infra.static_token_directory import StaticTokenDirectory
from app.server.context import AppContext
from app.server.hooks import create_bearer_hook
from app.server.routes import routes
from app.shared.errors import forbidden, project_not_found, unauthorized
from app.shared.users import User
from tenchi.client import Client
from tenchi.errors import AppError
from tenchi.server import create_app

TOKENS = {
    "alice-token": User(id="alice", name="Alice"),
    "bob-token": User(id="bob", name="Bob"),
}


@dataclass(frozen=True, slots=True)
class Harness:
    app: Starlette
    alice: Client
    bob: Client
    anonymous: Client


def make_app() -> Starlette:
    projects = MemoryProjectRepository()
    tasks = MemoryTaskRepository(projects)
    return create_app(
        routes=routes,
        context_factory=lambda: AppContext(projects=projects, tasks=tasks),
        hooks=[create_bearer_hook(StaticTokenDirectory(TOKENS))],
    )


def make_client(app: Starlette, token: str | None) -> Client:
    # unauthorized is declared client-side once, mirroring the server's
    # group-level route_group(errors=...) declaration.
    return Client(
        transport=httpx.ASGITransport(app=app),
        headers={"authorization": f"Bearer {token}"} if token else None,
        errors=(unauthorized,),
    )


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    app = make_app()
    clients = tuple(
        make_client(app, token) for token in ("alice-token", "bob-token", None)
    )
    alice, bob, anonymous = clients
    yield Harness(app=app, alice=alice, bob=bob, anonymous=anonymous)
    for client in clients:
        await client.aclose()


async def test_full_project_and_task_flow(harness: Harness) -> None:
    project = await harness.alice.call(
        create_project_contract, request=CreateProject(name="Launch")
    )
    assert project.owner_id == "alice"

    fetched = await harness.alice.call(
        get_project_contract, params=GetProjectParams(project_id=project.id)
    )
    assert fetched == project

    task = await harness.alice.call(
        create_task_contract,
        request=CreateTask(project_id=project.id, title="Ship it"),
    )
    assert task.status == TaskStatus.TODO

    updated = await harness.alice.call(
        update_task_contract,
        params=GetTaskParams(task_id=task.id),
        request=UpdateTask(status=TaskStatus.DONE),
    )
    assert updated.status == TaskStatus.DONE
    assert updated.title == "Ship it"

    page = await harness.alice.call(
        list_tasks_contract, query=ListTasksQuery(status=TaskStatus.DONE)
    )
    assert page.total == 1
    assert page.items[0].id == task.id


async def test_requests_without_a_token_are_unauthorized(
    harness: Harness,
) -> None:
    with pytest.raises(AppError) as excinfo:
        await harness.anonymous.call(list_projects_contract)

    assert excinfo.value.definition == unauthorized


async def test_unknown_tokens_are_unauthorized(harness: Harness) -> None:
    async with make_client(harness.app, "wrong-token") as intruder:
        with pytest.raises(AppError) as excinfo:
            await intruder.call(list_projects_contract)

    assert excinfo.value.definition == unauthorized


async def test_creating_a_task_in_anothers_project_is_forbidden(
    harness: Harness,
) -> None:
    project = await harness.alice.call(
        create_project_contract, request=CreateProject(name="Launch")
    )

    with pytest.raises(AppError) as excinfo:
        await harness.bob.call(
            create_task_contract,
            request=CreateTask(project_id=project.id, title="sabotage"),
        )

    assert excinfo.value.definition == forbidden


async def test_projects_are_isolated_between_users(harness: Harness) -> None:
    project = await harness.alice.call(
        create_project_contract, request=CreateProject(name="Launch")
    )

    assert await harness.bob.call(list_projects_contract) == []

    with pytest.raises(AppError) as excinfo:
        await harness.bob.call(
            get_project_contract, params=GetProjectParams(project_id=project.id)
        )
    assert excinfo.value.definition == project_not_found


async def test_pagination_over_http(harness: Harness) -> None:
    project = await harness.alice.call(
        create_project_contract, request=CreateProject(name="Launch")
    )
    for index in range(5):
        await harness.alice.call(
            create_task_contract,
            request=CreateTask(project_id=project.id, title=f"task {index}"),
        )

    page = await harness.alice.call(
        list_tasks_contract,
        query=ListTasksQuery(project_id=project.id, limit=2, offset=2),
    )

    assert page.total == 5
    assert [task.title for task in page.items] == ["task 2", "task 3"]
