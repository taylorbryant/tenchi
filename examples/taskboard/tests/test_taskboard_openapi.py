"""The taskboard OpenAPI document is valid and reflects auth and errors."""

from openapi_spec_validator import validate

from app.server.routes import api_routes
from tenchi.openapi import openapi_schema


def test_document_is_valid_and_documents_errors() -> None:
    document = openapi_schema(
        api_routes,
        title="Taskboard",
        version="0.1.0",
        security={"bearerAuth": {"type": "http", "scheme": "bearer"}},
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
    assert create_task["responses"]["201"]["headers"]["ETag"]["required"] is True
    assert create_task["responses"]["201"]["headers"]["Location"]["required"] is True

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
