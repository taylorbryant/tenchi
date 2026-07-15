from pathlib import Path

import pytest

from tenchi.cli import main
from tenchi.doctor import Finding, run_doctor

EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "todos"


@pytest.fixture
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    assert main(["new", "my_app"]) == 0
    root = tmp_path / "my_app"
    monkeypatch.chdir(root)
    return root


def messages(findings: list[Finding]) -> list[str]:
    return [finding.render() for finding in findings]


def test_example_app_is_clean() -> None:
    assert run_doctor(EXAMPLE_DIR) == []


def test_fresh_scaffold_is_clean(app_root: Path) -> None:
    assert run_doctor(app_root) == []


def test_use_case_importing_infra_is_flagged(app_root: Path) -> None:
    use_case = app_root / "app/features/todos/use_cases/create_todo.py"
    use_case.write_text(
        "from app.infra.memory_todo_repository import MemoryTodoRepository\n"
        + use_case.read_text()
    )

    findings = run_doctor(app_root)

    assert len(findings) == 1
    assert findings[0].path == "app/features/todos/use_cases/create_todo.py"
    assert findings[0].line == 1
    assert "use cases must not import concrete infrastructure" in findings[0].message
    assert "app.infra.memory_todo_repository" in findings[0].message


def test_from_package_import_submodule_is_flagged(app_root: Path) -> None:
    use_case = app_root / "app/features/todos/use_cases/list_todos.py"
    use_case.write_text(
        "from app.infra import memory_todo_repository  # noqa: F401\n"
        + use_case.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "imports app.infra.memory_todo_repository: use cases must not "
        "import concrete infrastructure" in m
        for m in messages(findings)
    )


def test_relative_import_into_infra_is_flagged(app_root: Path) -> None:
    use_case = app_root / "app/features/todos/use_cases/list_todos.py"
    use_case.write_text(
        "from ....infra.memory_todo_repository import MemoryTodoRepository\n"
        + use_case.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "app.infra.memory_todo_repository" in m
        and "use cases must not import concrete infrastructure" in m
        for m in messages(findings)
    )


def test_schemas_importing_http_runtime_is_flagged(app_root: Path) -> None:
    schemas = app_root / "app/features/todos/schemas.py"
    schemas.write_text(
        "from starlette.requests import Request  # noqa: F401\n" + schemas.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "starlette.requests" in m and "HTTP runtime" in m for m in messages(findings)
    )


def test_use_case_importing_tenchi_server_is_flagged(app_root: Path) -> None:
    use_case = app_root / "app/features/todos/use_cases/list_todos.py"
    use_case.write_text(
        "from tenchi.server import create_app  # noqa: F401\n" + use_case.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "tenchi.server" in m and "Tenchi server or client runtime" in m
        for m in messages(findings)
    )


def test_use_case_may_import_app_context_but_not_other_server_modules(
    app_root: Path,
) -> None:
    use_case = app_root / "app/features/todos/use_cases/list_todos.py"
    original = use_case.read_text()
    assert "from app.server.context import AppContext" in original
    use_case.write_text("import app.server.routes  # noqa: F401\n" + original)

    findings = run_doctor(app_root)

    assert any(
        "app.server.routes" in m and "no other server composition" in m
        for m in messages(findings)
    )
    # The context import present in the original file is never flagged.
    assert not any("imports app.server.context:" in m for m in messages(findings))


def test_feature_routes_importing_infra_is_flagged(app_root: Path) -> None:
    routes = app_root / "app/features/todos/routes.py"
    routes.write_text(
        "import app.infra.port_wiring  # noqa: F401\n" + routes.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "routes must not import concrete infrastructure" in m
        for m in messages(findings)
    )


def test_shared_importing_features_is_flagged(app_root: Path) -> None:
    errors = app_root / "app/shared/errors.py"
    errors.write_text(
        "from app.features.todos.schemas import Todo  # noqa: F401\n"
        + errors.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "shared code must not depend on features" in m for m in messages(findings)
    )


def test_infra_importing_use_cases_is_flagged(app_root: Path) -> None:
    wiring = app_root / "app/infra/port_wiring.py"
    wiring.write_text(
        "from app.features.todos.use_cases.create_todo import create_todo\n"
        + wiring.read_text()
    )

    findings = run_doctor(app_root)

    assert any("infrastructure implements ports" in m for m in messages(findings))


def test_infra_may_import_ports_schemas_and_external_libraries(
    app_root: Path,
) -> None:
    # The scaffold's port_wiring already imports ports and a sibling
    # adapter; add an external library import as well.
    wiring = app_root / "app/infra/port_wiring.py"
    wiring.write_text("import uuid  # noqa: F401\n" + wiring.read_text())

    assert run_doctor(app_root) == []


def test_policy_importing_infra_is_flagged(app_root: Path) -> None:
    policy = app_root / "app/features/todos/policy.py"
    policy.write_text(
        'import app.infra.port_wiring  # noqa: F401\n"""Authorization rules."""\n'
    )

    findings = run_doctor(app_root)

    assert any(
        "policies must not import infrastructure" in m for m in messages(findings)
    )


def test_policy_importing_context_is_flagged(app_root: Path) -> None:
    policy = app_root / "app/features/todos/policy.py"
    policy.write_text("from app.server.context import AppContext  # noqa: F401\n")

    findings = run_doctor(app_root)

    assert any("take their subjects as arguments" in m for m in messages(findings))


def test_use_cases_may_import_policies_across_features(app_root: Path) -> None:
    (app_root / "app/features/todos/policy.py").write_text(
        '"""Rules."""\n\nALLOW = True\n'
    )
    use_case = app_root / "app/features/todos/use_cases/list_todos.py"
    use_case.write_text(
        "from app.features.todos.policy import ALLOW  # noqa: F401\n"
        + use_case.read_text()
    )

    findings = run_doctor(app_root)

    # No dependency-direction finding for the policy import. (The
    # authorization consistency check may flag the *other* use case; that
    # behavior has its own tests.)
    assert not any("must not import" in m for m in messages(findings))


def test_unguarded_use_case_in_an_auth_using_app_is_flagged(
    app_root: Path,
) -> None:
    # Make one use case authorization-aware; the other becomes suspicious.
    create = app_root / "app/features/todos/use_cases/create_todo.py"
    create.write_text(
        create.read_text().replace(
            "return await context.todos.create(title=request.title)",
            "assert context.user is not None\n"
            "    return await context.todos.create(title=request.title)",
        )
    )

    findings = run_doctor(app_root)

    assert len(findings) == 1
    assert findings[0].path == "app/features/todos/use_cases/list_todos.py"
    assert "no authorization reference" in findings[0].message


def test_public_pragma_silences_the_authorization_check(app_root: Path) -> None:
    create = app_root / "app/features/todos/use_cases/create_todo.py"
    create.write_text(
        create.read_text().replace(
            "return await context.todos.create(title=request.title)",
            "assert context.user is not None\n"
            "    return await context.todos.create(title=request.title)",
        )
    )
    listing = app_root / "app/features/todos/use_cases/list_todos.py"
    listing.write_text("# doctor: public\n" + listing.read_text())

    assert run_doctor(app_root) == []


def test_apps_without_authorization_are_left_alone(app_root: Path) -> None:
    # The fresh scaffold has no authorization anywhere: no findings.
    assert run_doctor(app_root) == []


def test_policy_import_counts_as_authorization(app_root: Path) -> None:
    (app_root / "app/features/todos/policy.py").write_text(
        '"""Rules."""\n\nALLOW = True\n'
    )
    create = app_root / "app/features/todos/use_cases/create_todo.py"
    create.write_text(
        "from app.features.todos.policy import ALLOW  # noqa: F401\n"
        + create.read_text()
    )

    findings = run_doctor(app_root)

    assert len(findings) == 1
    assert findings[0].path == "app/features/todos/use_cases/list_todos.py"


def test_missing_prescribed_modules_are_flagged(app_root: Path) -> None:
    (app_root / "app/server/asgi.py").unlink()

    findings = run_doctor(app_root)

    assert messages(findings) == [
        "app/server/asgi.py  missing (expected by the prescribed structure)"
    ]


def test_feature_tests_are_exempt(app_root: Path) -> None:
    feature_test = app_root / "app/features/todos/tests/test_create_todo.py"
    assert "from app.infra" in feature_test.read_text()

    assert run_doctor(app_root) == []


def test_unparseable_module_is_reported_not_crashed(app_root: Path) -> None:
    (app_root / "app/features/todos/schemas.py").write_text("def broken(:\n")

    findings = run_doctor(app_root)

    assert any("could not parse" in m for m in messages(findings))


def test_doctor_cli_reports_and_exits_nonzero(
    app_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor"]) == 0
    assert "no problems found" in capsys.readouterr().out

    use_case = app_root / "app/features/todos/use_cases/create_todo.py"
    use_case.write_text("import app.infra.port_wiring\n" + use_case.read_text())

    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "app/features/todos/use_cases/create_todo.py:1" in out
    assert "1 problem(s) found" in out


def test_doctor_cli_requires_app_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["doctor"]) == 1
    assert "run this from an application root" in capsys.readouterr().err


def test_syntax_error_in_server_module_is_a_finding(app_root: Path) -> None:
    (app_root / "app/server/asgi.py").write_text("def broken(:\n")

    findings = run_doctor(app_root)

    assert any("could not parse" in m for m in messages(findings))


def test_from_app_server_import_context_is_allowed_in_use_cases(
    app_root: Path,
) -> None:
    use_case = app_root / "app/features/todos/use_cases/create_todo.py"
    source = use_case.read_text()
    source = source.replace(
        "from app.server.context import AppContext",
        "from app.server import context",
    ).replace("AppContext", "context.AppContext")
    use_case.write_text(source)

    assert run_doctor(app_root) == []


def test_pragma_inside_a_docstring_does_not_exempt(app_root: Path) -> None:
    guarded = app_root / "app/features/todos/use_cases/create_todo.py"
    guarded.write_text(
        "from app.server.context import AppContext\n\n\n"
        "async def create_todo(context: AppContext) -> None:\n"
        "    if context.user is None:\n"
        "        raise ValueError\n"
    )
    unguarded = app_root / "app/features/todos/use_cases/delete_todo.py"
    unguarded.write_text(
        '"""Not really `# doctor: public` — just mentions it."""\n\n\n'
        "async def delete_todo(context: object) -> None:\n"
        "    return None\n"
    )

    findings = run_doctor(app_root)

    assert any("no authorization reference" in m for m in messages(findings))


def test_domain_user_attribute_is_not_an_authorization_guard(
    app_root: Path,
) -> None:
    guarded = app_root / "app/features/todos/use_cases/create_todo.py"
    guarded.write_text(
        "from app.server.context import AppContext\n\n\n"
        "async def create_todo(context: AppContext) -> None:\n"
        "    if context.user is None:\n"
        "        raise ValueError\n"
    )
    # Reads .user off a domain object, never off the context: unguarded.
    sneaky = app_root / "app/features/todos/use_cases/delete_todo.py"
    sneaky.write_text(
        "async def delete_todo(request: object, context: object) -> None:\n"
        "    print(request.user)\n"
    )

    findings = run_doctor(app_root)

    assert any(
        "delete_todo" in m and "no authorization reference" in m
        for m in messages(findings)
    )


def test_shared_importing_policy_is_flagged(app_root: Path) -> None:
    (app_root / "app/features/todos/policy.py").write_text(
        "def can_edit() -> bool:\n    return True\n"
    )
    (app_root / "app/shared/helpers.py").write_text(
        "from app.features.todos.policy import can_edit\n"
    )

    findings = run_doctor(app_root)

    assert any("must not depend on features" in m for m in messages(findings))


def test_unrecognized_feature_module_is_flagged(app_root: Path) -> None:
    (app_root / "app/features/todos/helpers.py").write_text(
        "from app.infra.memory_todo_repository import MemoryTodoRepository\n"
    )

    findings = run_doctor(app_root)

    assert any("unrecognized feature module" in m for m in messages(findings))


def test_use_case_importing_tenchi_execution_is_flagged(app_root: Path) -> None:
    use_case = app_root / "app/features/todos/use_cases/create_todo.py"
    use_case.write_text("from tenchi.execution import execute\n" + use_case.read_text())

    findings = run_doctor(app_root)

    assert any(
        "must not import the Tenchi server or client runtime" in m
        for m in messages(findings)
    )


def test_root_reexport_of_runtime_names_is_flagged(app_root: Path) -> None:
    use_case = app_root / "app/features/todos/use_cases/list_todos.py"
    use_case.write_text(
        "from tenchi import execute  # noqa: F401\n" + use_case.read_text()
    )

    findings = run_doctor(app_root)

    assert any(
        "tenchi.execute" in m and "Tenchi server or client runtime" in m
        for m in messages(findings)
    )


def test_root_reexport_of_declaration_names_is_allowed(app_root: Path) -> None:
    contracts = app_root / "app/features/todos/contracts.py"
    contracts.write_text(
        "from tenchi import contract  # noqa: F401\n" + contracts.read_text()
    )

    assert run_doctor(app_root) == []
