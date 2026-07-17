"""The taskboard OpenAPI document is valid and reflects auth and errors."""

import json
from pathlib import Path

from openapi_spec_validator import validate

from app.server.routes import (
    OPENAPI_SECURITY,
    OPENAPI_TITLE,
    OPENAPI_VERSION,
    api_routes,
)
from tenchi.cli import main
from tenchi.openapi import openapi_schema

SNAPSHOT = Path(__file__).parent.parent / "openapi.json"


def test_openapi_snapshot_is_current() -> None:
    assert (
        main(
            [
                "openapi",
                "--routes",
                "app.server.routes:api_routes",
                "--title",
                OPENAPI_TITLE,
                "--version",
                OPENAPI_VERSION,
                "--security",
                json.dumps(OPENAPI_SECURITY),
                "--check",
                str(SNAPSHOT),
            ]
        )
        == 0
    )


def test_document_is_valid_and_documents_errors() -> None:
    document = openapi_schema(
        api_routes,
        title=OPENAPI_TITLE,
        version=OPENAPI_VERSION,
        security=OPENAPI_SECURITY,
    )

    validate(document)

    assert document["security"] == [{"bearerAuth": []}]
    assert document["components"]["securitySchemes"] == {
        "bearerAuth": {"type": "http", "scheme": "bearer"}
    }

    create_task = document["paths"]["/tasks"]["post"]
    assert "401" in create_task["responses"]
    assert "403" in create_task["responses"]
    assert "404" in create_task["responses"]
    assert "409" in create_task["responses"]
    idempotency_key = next(
        parameter
        for parameter in create_task["parameters"]
        if parameter["name"] == "idempotency-key"
    )
    assert idempotency_key["in"] == "header"
    assert idempotency_key["required"] is True
    assert idempotency_key["schema"]["minLength"] == 1
    assert idempotency_key["schema"]["maxLength"] == 128
    assert idempotency_key["schema"]["pattern"].endswith("*$")
    assert create_task["responses"]["201"]["headers"]["ETag"]["required"] is True
    assert create_task["responses"]["201"]["headers"]["Location"]["required"] is True
    assert create_task["x-timeout-seconds"] == 10.0
    assert "504" in create_task["responses"]

    add_member = document["paths"]["/projects/{project_id}/members"]["post"]
    assert {"200", "201"} <= set(add_member["responses"])

    get_task = document["paths"]["/tasks/{task_id}"]["get"]
    assert get_task["responses"]["200"]["headers"]["ETag"]["required"] is True

    update_task = document["paths"]["/tasks/{task_id}"]["patch"]
    if_match = next(
        parameter
        for parameter in update_task["parameters"]
        if parameter["name"] == "if-match"
    )
    assert if_match["in"] == "header"
    assert if_match["required"] is False
    assert '^"[1-9][0-9]*"$' in str(if_match["schema"])
    assert {"401", "403", "404", "412", "428"} <= set(update_task["responses"])
    assert update_task["responses"]["200"]["headers"]["ETag"]["required"] is True

    list_tasks = document["paths"]["/tasks"]["get"]
    parameter_names = {p["name"] for p in list_tasks["parameters"]}
    assert {"project_id", "status", "limit", "offset"} <= parameter_names

    schemas = document["components"]["schemas"]
    assert "Task" in schemas
    assert "TaskStatus" in schemas
    assert schemas["Task"]["properties"]["version"]["minimum"] == 1
