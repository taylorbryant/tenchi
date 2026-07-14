"""The taskboard OpenAPI document is valid and reflects auth and errors."""

from openapi_spec_validator import validate

from app.server.routes import api_routes
from tenchi.openapi import openapi_schema


def test_document_is_valid_and_documents_errors() -> None:
    document = openapi_schema(api_routes, title="Taskboard", version="0.1.0")

    validate(document)

    create_task = document["paths"]["/tasks"]["post"]
    assert "401" in create_task["responses"]
    assert "403" in create_task["responses"]
    assert "404" in create_task["responses"]

    list_tasks = document["paths"]["/tasks"]["get"]
    parameter_names = {p["name"] for p in list_tasks["parameters"]}
    assert {"project_id", "status", "limit", "offset"} <= parameter_names

    schemas = document["components"]["schemas"]
    assert "Task" in schemas
    assert "TaskStatus" in schemas
