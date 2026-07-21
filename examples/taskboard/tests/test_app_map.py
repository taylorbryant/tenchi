from pathlib import Path

from app.server.routes import api_routes
from tenchi._app_map import map_app, project_app_map

ROOT = Path(__file__).parent.parent


def test_taskboard_app_map_covers_real_application_relationships() -> None:
    result = map_app(ROOT, api_routes)
    edges = {(edge.kind, edge.source, edge.target) for edge in result.edges}

    assert result.summary.features == 2
    assert result.summary.contracts == 8
    assert result.summary.routes == 8
    assert result.summary.use_cases == 9
    assert result.summary.ports == 6
    assert result.summary.adapters == 11
    assert result.summary.entrypoints == 2
    assert result.diagnostics == ()
    assert result.unresolved == ()
    assert len(result.edges) == len(
        {(edge.kind, edge.source, edge.target) for edge in result.edges}
    )
    assert (
        "authorizes",
        "use-case:tasks.create_task",
        "policy:projects.ensure_can_write_project",
    ) in edges
    assert (
        "depends-on",
        "use-case:tasks.create_task",
        "port:tasks.TaskRepository",
    ) in edges
    assert (
        "implements",
        "adapter:app.infra.static_token_directory.StaticTokenDirectory",
        "port:app.shared.users.TokenDirectory",
    ) in edges
    assert (
        "depends-on",
        "entrypoint:app.server.worker",
        "use-case:projects.notify_member_added",
    ) in edges
    registered_adapters = {
        node.name
        for node in result.nodes
        if node.kind == "adapter" and node.status == "registered"
    }
    assert registered_adapters == {
        "SqliteNotificationLog",
        "SqliteOutbox",
        "SqliteProjectRepository",
        "SqliteTaskRepository",
        "SqliteTaskSearch",
        "StaticTokenDirectory",
    }
    task_repository_edge = next(
        edge
        for edge in result.edges
        if edge.source == "use-case:tasks.update_task"
        and edge.target == "port:tasks.TaskRepository"
    )
    assert task_repository_edge.evidence.line == 22
    notify_member_added = next(
        node
        for node in result.nodes
        if node.id == "use-case:projects.notify_member_added"
    )
    assert notify_member_added.status == "registered"
    member_contract = next(
        node
        for node in result.nodes
        if node.id == "contract:projects.add_project_member_contract"
    )
    assert dict(member_contract.details)["statuses"] == (201, 200)
    assert "status" not in dict(member_contract.details)


def test_taskboard_feature_projection_keeps_direct_cross_feature_policy() -> None:
    result = map_app(ROOT, api_routes)

    projected = project_app_map(result, feature="tasks")

    assert any(node.id == "feature:tasks" for node in projected.nodes)
    assert any(
        node.id == "policy:projects.ensure_can_write_project"
        for node in projected.nodes
    )
    assert not any(node.id == "route:POST /projects" for node in projected.nodes)
