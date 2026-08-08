from app.server.evaluations import evaluations
from app.server.routes import (
    OPENAPI_DESCRIPTION,
    OPENAPI_SECURITY,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
    api_routes,
)
from app.server.tools import tools
from openapi_spec_validator import validate
from tenchi.evaluations import evaluation_manifest
from tenchi.openapi import openapi_schema
from tenchi.snapshots import render_evaluation_snapshot, render_tool_snapshot
from tenchi.tools import tool_manifest


def test_openapi_is_valid_and_documents_security() -> None:
    document = openapi_schema(
        api_routes,
        title=OPENAPI_TITLE,
        version=OPENAPI_VERSION,
        description=OPENAPI_DESCRIPTION,
        security=OPENAPI_SECURITY,
    )

    validate(document)
    assert document["security"] == [{"bearerAuth": []}]
    assert "security" not in document["paths"]["/answers"]["post"]


def test_tool_and_evaluation_snapshots_are_current() -> None:
    with open("tools.json", encoding="utf-8") as snapshot:
        assert snapshot.read() == render_tool_snapshot(tool_manifest(tools))
    with open("evaluations.json", encoding="utf-8") as snapshot:
        assert snapshot.read() == render_evaluation_snapshot(
            evaluation_manifest(evaluations)
        )
