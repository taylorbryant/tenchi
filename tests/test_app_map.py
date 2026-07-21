import json
import shutil
import subprocess
import sys
from pathlib import Path

from app.server.routes import api_routes
from tenchi._app_map import (
    AppMapEdge,
    AppMapNode,
    AppMapResult,
    AppMapSource,
    AppMapSummary,
    AppMapUnresolvedReference,
    map_app,
    project_app_map,
)
from tenchi._cli_results import DiagnosticResult

EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "todos"
WIRE_SNAPSHOT = Path(__file__).with_name("app_map_snapshot.json")


def _tenchi(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tenchi.cli", *args],
        cwd=EXAMPLE_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


def test_app_map_is_deterministic_and_versioned() -> None:
    first = map_app(EXAMPLE_DIR, api_routes)
    second = map_app(EXAMPLE_DIR, api_routes)

    assert first.as_dict() == second.as_dict()
    assert first.schema_version == 1
    assert first.summary.features == 1
    assert first.summary.contracts == 3
    assert first.summary.routes == 3
    assert first.summary.use_cases == 3
    assert first.summary.ports == 1
    assert first.summary.adapters == 2
    assert first.diagnostics == ()
    assert first.unresolved == ()
    assert len({node.id for node in first.nodes}) == len(first.nodes)
    assert json.loads(json.dumps(first.as_dict()))["schema_version"] == 1


def test_app_map_json_wire_format_matches_snapshot() -> None:
    node_kinds = (
        "feature",
        "contract",
        "route",
        "use-case",
        "policy",
        "port",
        "adapter",
        "context",
        "entrypoint",
        "test",
    )
    nodes = tuple(
        AppMapNode(
            id=f"{kind}:example",
            kind=kind,
            name=f"example-{kind}",
            feature="example" if index < 7 else None,
            source=AppMapSource(
                path=f"app/{kind}.py",
                line=index + 1 if index % 2 == 0 else None,
                symbol=f"example_{kind}" if index % 2 == 0 else None,
            ),
            status="registered" if index % 2 == 0 else "declared",
            details=(
                ("boolean", True),
                ("empty", None),
                ("integer", 200),
                ("integers", (200, 201)),
                ("number", 1.5),
                ("string", "value"),
                ("strings", ("one", "two")),
            )
            if index == 0
            else (),
        )
        for index, kind in enumerate(node_kinds)
    )
    edge_kinds = (
        "owns",
        "binds",
        "depends-on",
        "implements",
        "authorizes",
        "contains-test",
    )
    edges = tuple(
        AppMapEdge(
            kind=kind,
            source=nodes[index].id,
            target=nodes[index + 1].id,
            evidence=AppMapSource(path="app/wiring.py", line=index + 10),
            confidence="exact" if index % 2 == 0 else "inferred",
        )
        for index, kind in enumerate(edge_kinds)
    )
    result = AppMapResult(
        root="<ROOT>",
        summary=AppMapSummary(
            features=1,
            contracts=1,
            routes=1,
            use_cases=1,
            policies=1,
            ports=1,
            adapters=1,
            contexts=1,
            entrypoints=1,
            tests=1,
            diagnostics=1,
            unresolved=1,
        ),
        nodes=nodes,
        edges=edges,
        diagnostics=(
            DiagnosticResult(
                code="TENCHI_DOCTOR_EXAMPLE",
                severity="warning",
                message="example diagnostic",
                path="app/example.py",
                line=None,
            ),
        ),
        unresolved=(
            AppMapUnresolvedReference(
                code="TENCHI_MAP_EXAMPLE",
                message="example unresolved reference",
                source=AppMapSource(path="app/example.py", line=42),
            ),
        ),
    )

    assert result.as_dict() == json.loads(WIRE_SNAPSHOT.read_text(encoding="utf-8"))


def test_app_map_connects_routes_ports_adapters_and_tests() -> None:
    result = map_app(EXAMPLE_DIR, api_routes)
    edges = {(edge.kind, edge.source, edge.target) for edge in result.edges}

    assert (
        "binds",
        "route:POST /todos",
        "contract:todos.create_todo_contract",
    ) in edges
    assert (
        "binds",
        "route:POST /todos",
        "use-case:todos.create_todo",
    ) in edges
    assert (
        "depends-on",
        "use-case:todos.create_todo",
        "port:todos.TodoRepository",
    ) in edges
    assert (
        "implements",
        "adapter:app.infra.sqlite_todo_repository.SqliteTodoRepository",
        "port:todos.TodoRepository",
    ) in edges
    assert (
        "depends-on",
        "entrypoint:app.server.asgi",
        "adapter:app.infra.sqlite_todo_repository.SqliteTodoRepository",
    ) in edges
    assert (
        "contains-test",
        "feature:todos",
        "test:app/features/todos/tests/test_create_todo.py",
    ) in edges

    route = next(node for node in result.nodes if node.id == "route:POST /todos")
    assert route.status == "registered"
    assert route.source.path == "app/features/todos/routes.py"
    assert route.source.line is not None
    adapters = {
        node.name: node.status for node in result.nodes if node.kind == "adapter"
    }
    assert adapters == {
        "MemoryTodoRepository": "declared",
        "SqliteTodoRepository": "registered",
    }


def test_app_map_projection_filters_features_and_kinds() -> None:
    result = map_app(EXAMPLE_DIR, api_routes)

    projected = project_app_map(
        result,
        feature="todos",
        kinds=("route", "use-case", "port"),
    )

    assert projected.summary.features == 0
    assert projected.summary.routes == 3
    assert projected.summary.use_cases == 3
    assert projected.summary.ports == 1
    assert {node.kind for node in projected.nodes} == {"route", "use-case", "port"}
    assert all(
        edge.source in {node.id for node in projected.nodes}
        and edge.target in {node.id for node in projected.nodes}
        for edge in projected.edges
    )


def test_app_map_keeps_unregistered_contracts_declared(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    contracts = root / "app/features/todos/contracts.py"
    contracts.write_text(
        contracts.read_text()
        + '\nunused_contract = contract(method="DELETE", path="/unused")\n',
        encoding="utf-8",
    )

    result = map_app(root, api_routes)

    unused = next(
        node for node in result.nodes if node.id == "contract:todos.unused_contract"
    )
    assert unused.status == "declared"
    assert not any(
        edge.kind == "binds" and edge.target == unused.id for edge in result.edges
    )


def test_app_map_does_not_register_an_unused_adapter_import(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    wiring = root / "app/infra/port_wiring.py"
    wiring.write_text(
        "from .memory_todo_repository import MemoryTodoRepository\n"
        + wiring.read_text(),
        encoding="utf-8",
    )

    result = map_app(root, api_routes)

    memory = next(
        node
        for node in result.nodes
        if node.id == "adapter:app.infra.memory_todo_repository.MemoryTodoRepository"
    )
    assert memory.status == "declared"


def test_app_map_does_not_register_adapters_for_an_unrelated_helper(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    bundle = root / "app/infra/bundle_repositories.py"
    bundle.write_text(
        "class FirstTodoRepository:\n    pass\n\n"
        "class SecondTodoRepository:\n    pass\n\n"
        "async def ensure_schema() -> None:\n    pass\n",
        encoding="utf-8",
    )
    wiring = root / "app/infra/port_wiring.py"
    wiring.write_text(
        "from .bundle_repositories import ensure_schema as ensure_bundle_schema\n"
        + wiring.read_text()
        + "\nasync def ensure_bundle() -> None:\n"
        "    await ensure_bundle_schema()\n",
        encoding="utf-8",
    )

    result = map_app(root, api_routes)

    statuses = {
        node.name: node.status for node in result.nodes if node.kind == "adapter"
    }
    assert statuses["FirstTodoRepository"] == "declared"
    assert statuses["SecondTodoRepository"] == "declared"


def test_app_map_registers_only_adapters_reachable_from_an_entrypoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    bundle = root / "app/infra/bundle_repositories.py"
    bundle.write_text(
        "class FirstTodoRepository:\n    pass\n\n"
        "class SecondTodoRepository:\n    pass\n\n"
        "def open_first(\n"
        "    candidate: SecondTodoRepository | None = None,\n"
        ") -> FirstTodoRepository:\n"
        "    if isinstance(candidate, SecondTodoRepository):\n"
        "        raise RuntimeError\n"
        "    return FirstTodoRepository()\n\n"
        "def open_second() -> SecondTodoRepository:\n"
        "    return SecondTodoRepository()\n",
        encoding="utf-8",
    )
    wiring = root / "app/infra/port_wiring.py"
    wiring.write_text(
        wiring.read_text()
        + "\nfrom .bundle_repositories import open_first, open_second\n\n"
        "def primary_repository() -> object:\n"
        "    return open_first()\n\n"
        "def alternate_repository() -> object:\n"
        "    return open_second()\n",
        encoding="utf-8",
    )
    asgi = root / "app/server/asgi.py"
    asgi.write_text(
        asgi.read_text()
        .replace(
            "from app.infra.port_wiring import ensure_schema, open_todo_repository",
            "from app.infra.port_wiring import (\n"
            "    ensure_schema,\n"
            "    open_todo_repository,\n"
            "    primary_repository,\n"
            ")",
        )
        .replace(
            "async with open_todo_repository(database_path) as todos:",
            "primary_repository()\n"
            "    async with open_todo_repository(database_path) as todos:",
        )
        + "\nfrom app.infra.bundle_repositories import (\n"
        "    open_second as alternate_factory,\n"
        ")\n"
        "ALTERNATE_FACTORY = alternate_factory\n",
        encoding="utf-8",
    )

    result = map_app(root, api_routes)
    statuses = {
        node.name: node.status for node in result.nodes if node.kind == "adapter"
    }

    assert statuses["FirstTodoRepository"] == "registered"
    assert statuses["SecondTodoRepository"] == "declared"


def test_app_map_scopes_imports_to_the_definition_that_uses_them(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    policy = root / "app/features/todos/policy.py"
    policy.write_text(
        "def ensure_allowed() -> None:\n    pass\n",
        encoding="utf-8",
    )
    use_cases = root / "app/features/todos/use_cases/mixed.py"
    use_cases.write_text(
        "from ..policy import ensure_allowed\n\n"
        "async def guarded() -> None:\n    ensure_allowed()\n\n"
        "async def public() -> None:\n    pass\n\n"
        "def synchronous_helper() -> None:\n    ensure_allowed()\n",
        encoding="utf-8",
    )
    worker = root / "app/server/unused_worker.py"
    worker.write_text(
        "from app.features.todos.use_cases.mixed import public\n\n"
        'if __name__ == "__main__":\n    pass\n',
        encoding="utf-8",
    )

    result = map_app(root, api_routes)
    edges = {(edge.kind, edge.source, edge.target) for edge in result.edges}

    assert (
        "authorizes",
        "use-case:todos.guarded",
        "policy:todos.ensure_allowed",
    ) in edges
    assert (
        "authorizes",
        "use-case:todos.public",
        "policy:todos.ensure_allowed",
    ) not in edges
    assert not any(
        node.id == "use-case:todos.synchronous_helper" for node in result.nodes
    )
    public = next(node for node in result.nodes if node.id == "use-case:todos.public")
    assert public.status == "declared"
    assert (
        "depends-on",
        "entrypoint:app.server.unused_worker",
        "use-case:todos.public",
    ) not in edges


def test_app_map_resolves_type_checking_local_and_module_imports(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    policy = root / "app/features/todos/policy.py"
    policy.write_text(
        "def ensure_allowed() -> None:\n    pass\n",
        encoding="utf-8",
    )
    use_cases = root / "app/features/todos/use_cases/import_forms.py"
    use_cases.write_text(
        "from typing import TYPE_CHECKING\n"
        "from .. import policy as policy_module\n\n"
        "if TYPE_CHECKING:\n"
        "    from app.server.context import AppContext\n\n"
        "async def qualified(context: AppContext) -> None:\n"
        "    policy_module.ensure_allowed()\n\n"
        "async def local() -> None:\n"
        "    from ..policy import ensure_allowed\n"
        "    ensure_allowed()\n",
        encoding="utf-8",
    )

    result = map_app(root, api_routes)
    edges = {(edge.kind, edge.source, edge.target) for edge in result.edges}

    assert (
        "authorizes",
        "use-case:todos.qualified",
        "policy:todos.ensure_allowed",
    ) in edges
    assert (
        "depends-on",
        "use-case:todos.qualified",
        "context:AppContext",
    ) in edges
    assert (
        "authorizes",
        "use-case:todos.local",
        "policy:todos.ensure_allowed",
    ) in edges


def test_app_map_does_not_classify_async_generators_as_use_cases(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    use_case = root / "app/features/todos/use_cases/events.py"
    use_case.write_text(
        "from collections.abc import AsyncIterator\n\n"
        "async def events() -> AsyncIterator[int]:\n"
        "    yield 1\n",
        encoding="utf-8",
    )

    result = map_app(root, api_routes)

    assert not any(node.id == "use-case:todos.events" for node in result.nodes)


def test_app_map_includes_doctor_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    use_case = root / "app/features/todos/use_cases/create_todo.py"
    use_case.write_text(
        "import app.infra.port_wiring\n" + use_case.read_text(),
        encoding="utf-8",
    )

    result = map_app(root, api_routes)

    assert any(
        item.code == "TENCHI_DOCTOR_FORBIDDEN_IMPORT"
        and item.path == "app/features/todos/use_cases/create_todo.py"
        for item in result.diagnostics
    )


def test_feature_projection_keeps_diagnostics_for_related_nodes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    adapter = root / "app/infra/memory_todo_repository.py"
    adapter.write_text(
        "import app.server.context\n" + adapter.read_text(),
        encoding="utf-8",
    )

    projected = project_app_map(map_app(root, api_routes), feature="todos")

    assert any(
        item.code == "TENCHI_DOCTOR_FORBIDDEN_IMPORT"
        and item.path == "app/infra/memory_todo_repository.py"
        for item in projected.diagnostics
    )


def test_source_only_contracts_omit_non_literal_metadata(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    contracts = root / "app/features/todos/contracts.py"
    contracts.write_text(
        contracts.read_text()
        + """
METHOD = "DELETE"
PATH = "/dynamic"
STATUS = 204
IS_PUBLIC = True
TAGS = ("dynamic",)
dynamic_contract = contract(
    method=METHOD,
    path=PATH,
    status=STATUS,
    public=IS_PUBLIC,
    tags=TAGS,
)
""",
        encoding="utf-8",
    )

    result = map_app(root, api_routes)

    dynamic = next(
        node for node in result.nodes if node.id == "contract:todos.dynamic_contract"
    )
    assert dict(dynamic.details) == {"export_name": "dynamic_contract"}


def test_app_map_reports_source_it_cannot_parse(tmp_path: Path) -> None:
    root = tmp_path / "app"
    shutil.copytree(EXAMPLE_DIR, root)
    broken_test = root / "tests/test_broken.py"
    broken_test.write_text("def broken(:\n", encoding="utf-8")

    result = map_app(root, api_routes)

    assert any(
        item.code == "TENCHI_MAP_SOURCE_PARSE_ERROR"
        and item.source.path == "tests/test_broken.py"
        and item.source.line == 1
        for item in result.unresolved
    )


def test_map_cli_supports_json_human_and_projections() -> None:
    complete = _tenchi("map", "--json")
    assert complete.returncode == 0, complete.stderr
    payload = json.loads(complete.stdout)
    assert payload["schema_version"] == 1
    assert payload["summary"]["routes"] == 3

    projected = _tenchi(
        "map",
        "--feature",
        "todos",
        "--kind",
        "route,use-case,port",
        "--json",
    )
    assert projected.returncode == 0, projected.stderr
    projected_payload = json.loads(projected.stdout)
    assert {node["kind"] for node in projected_payload["nodes"]} == {
        "route",
        "use-case",
        "port",
    }

    human = _tenchi("map", "--kind", "route")
    assert human.returncode == 0, human.stderr
    assert "Tenchi app map" in human.stdout
    assert "POST /todos" in human.stdout

    relationships = _tenchi("map", "--kind", "route,contract")
    assert relationships.returncode == 0, relationships.stderr
    assert "route:POST /todos --binds--> contract:todos.create_todo_contract" in (
        relationships.stdout
    )


def test_map_cli_rejects_unknown_features_and_kinds() -> None:
    feature = _tenchi("map", "--feature", "missing")
    assert feature.returncode == 1
    assert "available features: todos" in feature.stderr

    kind = _tenchi("map", "--kind", "widget")
    assert kind.returncode == 2
    assert "unknown app-map node kind 'widget'" in kind.stderr
