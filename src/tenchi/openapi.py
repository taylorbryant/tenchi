"""OpenAPI 3.1 generation from contracts.

Contracts already carry everything the document needs — method, path,
request/params/query/response types, success status, and declared errors —
so :func:`openapi_schema` is a pure function from a route group to a dict.
:func:`openapi_route` wraps that dict in an ordinary Tenchi route so the
document is served by the same machinery it describes.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from . import errors as tenchi_errors
from .contracts import Contract, contract
from .errors import ErrorDef
from .routes import Route, RouteGroup, route

_ERROR_COMPONENT = "ErrorResponse"

_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": _ERROR_COMPONENT,
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "details": {},
    },
    "required": ["code", "message"],
}


def openapi_schema(
    routes: RouteGroup,
    *,
    title: str,
    version: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Build an OpenAPI 3.1 document for every route in the group."""
    components: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    operation_ids: dict[str, int] = {}

    for item in routes:
        declared = item.contract
        operation = _operation(item, components, operation_ids)
        paths.setdefault(declared.path, {})[declared.method.lower()] = operation

    info: dict[str, Any] = {"title": title, "version": version}
    if description is not None:
        info["description"] = description

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": info,
        "paths": paths,
    }
    if components:
        document["components"] = {"schemas": components}
    return document


def openapi_route(
    routes: RouteGroup,
    *,
    title: str,
    version: str,
    description: str | None = None,
    path: str = "/openapi.json",
) -> Route:
    """Build a route serving the OpenAPI document for ``routes``.

    The document is generated once at composition time and covers exactly
    the given group, so the serving route does not document itself. Compose
    it alongside the application's routes:

        api_routes = route_group(todo_routes)
        routes = route_group(api_routes, openapi_route(api_routes, ...))
    """
    document = openapi_schema(
        routes, title=title, version=version, description=description
    )

    async def get_openapi(context: object) -> dict[str, Any]:
        return document

    return route(
        contract(method="GET", path=path, response=dict[str, Any]),
        get_openapi,
    )


def _operation(
    item: Route,
    components: dict[str, Any],
    operation_ids: dict[str, int],
) -> dict[str, Any]:
    declared = item.contract
    operation: dict[str, Any] = {
        "operationId": _unique_operation_id(item, operation_ids)
    }

    parameters: list[dict[str, Any]] = []
    if declared.params is not None:
        parameters.extend(_parameters(declared.params, "path", components))
    if declared.query is not None:
        parameters.extend(_parameters(declared.query, "query", components))
    if parameters:
        operation["parameters"] = parameters

    if declared.request is not None:
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _json_schema(
                        declared.request, components, mode="validation"
                    )
                }
            },
        }

    operation["responses"] = _responses(declared, components)
    return operation


def _responses(declared: Contract[Any], components: dict[str, Any]) -> dict[str, Any]:
    responses: dict[str, Any] = {}

    success: dict[str, Any] = {"description": "Successful response"}
    if declared.response is not None:
        success["content"] = {
            "application/json": {
                "schema": _json_schema(
                    declared.response, components, mode="serialization"
                )
            }
        }
    responses[str(declared.status)] = success

    errors_by_status: dict[int, list[ErrorDef]] = {}
    for definition in declared.errors:
        errors_by_status.setdefault(definition.status, []).append(definition)
    for status, definitions in errors_by_status.items():
        responses[str(status)] = _error_response(definitions, components)

    has_validated_input = (
        declared.request is not None
        or declared.params is not None
        or declared.query is not None
    )
    validation_status = str(tenchi_errors.validation_error.status)
    if has_validated_input and validation_status not in responses:
        responses[validation_status] = _error_response(
            [tenchi_errors.validation_error], components
        )

    return responses


def _error_response(
    definitions: list[ErrorDef], components: dict[str, Any]
) -> dict[str, Any]:
    components.setdefault(_ERROR_COMPONENT, dict(_ERROR_SCHEMA))
    description = "; ".join(
        f"{definition.code}: {definition.message}" for definition in definitions
    )
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": f"#/components/schemas/{_ERROR_COMPONENT}"}
            }
        },
    }


def _parameters(
    annotation: type[Any],
    location: str,
    components: dict[str, Any],
) -> list[dict[str, Any]]:
    schema = _json_schema(annotation, components, mode="validation")
    required = set(schema.get("required", []))
    parameters: list[dict[str, Any]] = []
    for name, property_schema in schema.get("properties", {}).items():
        parameters.append(
            {
                "name": name,
                "in": location,
                "required": location == "path" or name in required,
                "schema": property_schema,
            }
        )
    return parameters


def _json_schema(
    annotation: type[Any],
    components: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    schema = TypeAdapter(annotation).json_schema(
        mode="serialization" if mode == "serialization" else "validation",
        ref_template="#/components/schemas/{model}",
    )
    for name, definition in schema.pop("$defs", {}).items():
        components.setdefault(name, definition)
    return schema


def _unique_operation_id(item: Route, operation_ids: dict[str, int]) -> str:
    base = getattr(item.use_case, "__name__", None) or "operation"
    count = operation_ids.get(base, 0)
    operation_ids[base] = count + 1
    return base if count == 0 else f"{base}_{count + 1}"
