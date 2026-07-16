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
    get_task_contract,
    list_tasks_contract,
    update_task_contract,
)
from app.features.tasks.schemas import (
    CreateTask,
    GetTaskParams,
    ListTasksQuery,
    TaskStatus,
    UpdateTask,
    UpdateTaskHeaders,
)
from app.infra.memory_repositories import (
    MemoryNotificationLog,
    MemoryOutbox,
    MemoryProjectRepository,
    MemoryTaskRepository,
    MemoryTaskSearch,
)
from app.infra.static_token_directory import StaticTokenDirectory
from app.server.context import AppContext
from app.server.hooks import create_bearer_hook
from app.server.routes import routes
from app.shared.errors import (
    forbidden,
    precondition_failed,
    project_not_found,
    unauthorized,
)
from app.shared.users import User
from tenchi.client import Client
from tenchi.errors import ERROR_SOURCE_HEADER, AppError
from tenchi.server import create_app
from tenchi.testing import open_http

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
        context_factory=lambda: AppContext(
            projects=projects,
            tasks=tasks,
            task_search=MemoryTaskSearch(projects, tasks),
            outbox=MemoryOutbox(),
            notifications=MemoryNotificationLog(),
        ),
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
    project_response = await harness.alice.call_with_response(
        create_project_contract, request=CreateProject(name="Launch")
    )
    project = project_response.body
    assert project.owner_id == "alice"
    assert project_response.headers.location == f"/projects/{project.id}"

    fetched = await harness.alice.call(
        get_project_contract, params=GetProjectParams(project_id=project.id)
    )
    assert fetched == project

    task_response = await harness.alice.call_with_response(
        create_task_contract,
        request=CreateTask(project_id=project.id, title="Ship it"),
    )
    task = task_response.body
    assert task.status == TaskStatus.TODO
    assert task.version == 1
    assert task_response.headers.etag == '"1"'
    assert task_response.headers.location == f"/tasks/{task.id}"

    fetched_task = await harness.alice.call_with_response(
        get_task_contract,
        params=GetTaskParams(task_id=task.id),
    )
    assert fetched_task.body == task
    assert fetched_task.headers.etag == task_response.headers.etag

    updated_response = await harness.alice.call_with_response(
        update_task_contract,
        params=GetTaskParams(task_id=task.id),
        headers=UpdateTaskHeaders(if_match=task_response.headers.etag),
        request=UpdateTask(status=TaskStatus.DONE),
    )
    updated = updated_response.body
    assert updated.status == TaskStatus.DONE
    assert updated.title == "Ship it"
    assert updated.version == 2
    assert updated_response.headers.etag == '"2"'

    page = await harness.alice.call(
        list_tasks_contract, query=ListTasksQuery(status=TaskStatus.DONE)
    )
    assert page.total == 1
    assert page.items[0].id == task.id
    assert page.items[0].version == updated.version


async def test_typed_client_rejects_a_stale_task_update(harness: Harness) -> None:
    project = await harness.alice.call(
        create_project_contract,
        request=CreateProject(name="Launch"),
    )
    created = await harness.alice.call_with_response(
        create_task_contract,
        request=CreateTask(project_id=project.id, title="original"),
    )
    old_tag = created.headers.etag

    first = await harness.alice.call_with_response(
        update_task_contract,
        params=GetTaskParams(task_id=created.body.id),
        headers=UpdateTaskHeaders(if_match=old_tag),
        request=UpdateTask(title="first writer"),
    )
    with pytest.raises(AppError) as excinfo:
        await harness.alice.call(
            update_task_contract,
            params=GetTaskParams(task_id=created.body.id),
            headers=UpdateTaskHeaders(if_match=old_tag),
            request=UpdateTask(title="stale writer"),
        )

    assert excinfo.value.definition == precondition_failed
    fetched = await harness.alice.call(
        get_task_contract,
        params=GetTaskParams(task_id=created.body.id),
    )
    assert fetched == first.body


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


async def test_health_is_public_and_runs_the_database_check() -> None:
    async with open_http(make_app()) as http:
        response = await http.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


async def test_raw_http_enforces_if_match_and_returns_etags() -> None:
    authorization = {"authorization": "Bearer alice-token"}
    async with open_http(make_app()) as http:
        project_response = await http.post(
            "/projects",
            headers=authorization,
            json={"name": "Launch"},
        )
        project_id = project_response.json()["id"]
        created = await http.post(
            "/tasks",
            headers=authorization,
            json={"project_id": project_id, "title": "original"},
        )
        task_id = created.json()["id"]

        assert created.status_code == 201
        assert created.headers["etag"] == '"1"'
        assert created.headers["location"] == f"/tasks/{task_id}"
        assert created.json()["version"] == 1

        missing = await http.patch(
            f"/tasks/{task_id}",
            headers=authorization,
            json={"title": "missing precondition"},
        )
        assert missing.status_code == 428
        assert missing.headers[ERROR_SOURCE_HEADER] == "app"
        assert missing.json()["code"] == "PRECONDITION_REQUIRED"

        malformed = await http.patch(
            f"/tasks/{task_id}",
            headers={**authorization, "if-match": 'W/"1"'},
            json={"title": "weak tag"},
        )
        assert malformed.status_code == 422
        assert malformed.headers[ERROR_SOURCE_HEADER] == "framework"
        assert malformed.json()["code"] == "VALIDATION_ERROR"

        updated = await http.patch(
            f"/tasks/{task_id}",
            headers={**authorization, "if-match": '"1"'},
            json={"title": "first writer"},
        )
        assert updated.status_code == 200
        assert updated.headers["etag"] == '"2"'
        assert updated.json()["version"] == 2

        stale = await http.patch(
            f"/tasks/{task_id}",
            headers={**authorization, "if-match": '"1"'},
            json={"title": "stale writer"},
        )
        assert stale.status_code == 412
        assert stale.headers[ERROR_SOURCE_HEADER] == "app"
        assert stale.json()["code"] == "PRECONDITION_FAILED"

        fetched = await http.get(f"/tasks/{task_id}", headers=authorization)
        assert fetched.json()["title"] == "first writer"
        assert fetched.headers["etag"] == '"2"'
