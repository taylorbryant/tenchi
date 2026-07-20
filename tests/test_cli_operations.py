import json
from pathlib import Path

from tenchi._cli_operations import openapi_defaults


def test_openapi_defaults_read_literal_metadata_without_importing_the_app(
    tmp_path: Path,
) -> None:
    routes = tmp_path / "app/server/routes.py"
    routes.parent.mkdir(parents=True)
    routes.write_text(
        """\
raise RuntimeError("must not be imported")
OPENAPI_TITLE: str = "Literal API"
OPENAPI_VERSION = "2.3.4"
OPENAPI_DESCRIPTION = "Literal description"
OPENAPI_SECURITY = {"bearerAuth": {"type": "http", "scheme": "bearer"}}
""",
        encoding="utf-8",
    )

    title, version, description, security = openapi_defaults(
        tmp_path,
        routes="app.server.routes:api_routes",
        title=None,
        version=None,
        description=None,
        security_json=None,
    )

    assert title == "Literal API"
    assert version == "2.3.4"
    assert description == "Literal description"
    assert json.loads(security or "null") == {
        "bearerAuth": {"type": "http", "scheme": "bearer"}
    }


def test_openapi_default_flags_override_source_metadata(tmp_path: Path) -> None:
    routes = tmp_path / "app/server/routes.py"
    routes.parent.mkdir(parents=True)
    routes.write_text(
        'OPENAPI_TITLE = "Source"\nOPENAPI_VERSION = "1.0.0"\n',
        encoding="utf-8",
    )

    result = openapi_defaults(
        tmp_path,
        routes="app.server.routes:api_routes",
        title="Flag",
        version="9.0.0",
        description="Flag description",
        security_json='{"custom":{}}',
    )

    assert result == ("Flag", "9.0.0", "Flag description", '{"custom":{}}')
