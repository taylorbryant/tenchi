"""OpenAPI 3.1 generation from contracts.

Contracts already carry everything the document needs — method, path,
request/params/query/response types, successful response headers and statuses,
deadlines, and declared errors — so :func:`openapi_schema` is a pure function
from a route group to a dict.
:func:`openapi_route` wraps that dict in an ordinary Tenchi route so the
document is served by the same machinery it describes.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from datetime import UTC
from typing import Any, cast

from pydantic import TypeAdapter

from . import errors as tenchi_errors
from .contracts import (
    Contract,
    _object_schema,  # pyright: ignore[reportPrivateUsage]
    _response_header_fields,  # pyright: ignore[reportPrivateUsage]
    contract,
)
from .errors import ConfigurationError, ErrorDef
from .responses import ResponseDef
from .routes import (
    Route,
    RouteGroup,
    _document_path,  # pyright: ignore[reportPrivateUsage]
    _validate_route_identities,  # pyright: ignore[reportPrivateUsage]
    route,
)

_ERROR_COMPONENT = "ErrorResponse"

_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": _ERROR_COMPONENT,
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "details": {},
        "request_id": {"type": "string"},
    },
    "required": ["code", "message"],
}


def openapi_schema(
    routes: RouteGroup,
    *,
    title: str,
    version: str,
    description: str | None = None,
    security: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an OpenAPI 3.1 document for every route in the group.

    ``security`` maps scheme names to OpenAPI security scheme objects, for
    example ``{"bearerAuth": {"type": "http", "scheme": "bearer"}}`` or
    ``{"apiKeyAuth": {"type": "apiKey", "in": "header", "name":
    "x-api-key"}}``. When given, every scheme is required globally and
    operations whose contracts declare ``public=True`` are exempted with an
    empty per-operation security list. Authentication hooks can inspect the
    same metadata, keeping runtime access and documentation aligned.

    Operations with request bodies document the framework's 415 for missing or
    mismatched media types and its 413 because body caps are on by default.
    This is a pure function of the route group — it cannot see
    ``create_app(max_request_bytes=None)`` — so an app that disables caps
    entirely (and sets no per-contract ceilings) over-documents the 413.
    """
    _validate_document_metadata(title=title, version=version, description=description)
    security_schemes = _validated_security(security)
    components: dict[str, Any] = {}
    paths: dict[str, dict[str, Any]] = {}
    operation_ids: dict[str, int] = {}
    error_schemas: list[dict[str, Any]] = []
    _validate_route_identities(routes, label="openapi_schema")

    for item in routes:
        declared = item.contract
        document_path = _document_path(declared.path)
        operations = paths.setdefault(document_path, {})
        try:
            operation = _operation(item, components, operation_ids, error_schemas)
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(
                f"openapi_schema: route {declared.name!r} has a type Pydantic "
                f"cannot document: {exc}"
            ) from exc
        if security_schemes and declared.public:
            operation["security"] = []
        operations[declared.method.lower()] = operation

    if error_schemas:
        component_name = _error_component_name(components)
        reference = f"#/components/schemas/{component_name}"
        for schema in error_schemas:
            schema["$ref"] = reference

    info: dict[str, Any] = {"title": title, "version": version}
    if description is not None:
        info["description"] = description

    document: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": info,
        "paths": paths,
    }
    if security_schemes:
        document["security"] = [{name: [] for name in security_schemes}]
    if components or security_schemes:
        document["components"] = {}
        if components:
            document["components"]["schemas"] = components
        if security_schemes:
            document["components"]["securitySchemes"] = security_schemes
    return document


def openapi_route(
    routes: RouteGroup,
    *,
    title: str,
    version: str,
    description: str | None = None,
    security: Mapping[str, Mapping[str, Any]] | None = None,
    path: str = "/openapi.json",
    tags: Sequence[str] = ("docs",),
    public: bool = True,
) -> Route:
    """Build a route serving the OpenAPI document for ``routes``.

    The document is generated once at composition time and covers exactly
    the given group, so the serving route does not document itself. Compose
    it alongside the application's routes:

        api_routes = route_group(todo_routes)
        routes = route_group(api_routes, openapi_route(api_routes, ...))

    The serving route is public by default so authentication hooks can exempt
    it by contract metadata rather than by path or documentation tag. Pass
    ``public=False`` when the document itself requires authentication.
    """
    document = openapi_schema(
        routes,
        title=title,
        version=version,
        description=description,
        security=security,
    )

    async def get_openapi(context: object) -> dict[str, Any]:
        return document

    return route(
        contract(
            method="GET",
            path=path,
            response=dict[str, Any],
            tags=tags,
            public=public,
        ),
        get_openapi,
    )


def _validate_document_metadata(
    *, title: object, version: object, description: object
) -> None:
    for label, value in (("title", title), ("version", version)):
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(
                f"openapi_schema: {label} must be a non-empty string"
            )
    if description is not None and not isinstance(description, str):
        raise ConfigurationError("openapi_schema: description must be a string or None")


def _validated_security(value: object) -> dict[str, dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigurationError(
            "openapi_schema: security must be a mapping of scheme names to mappings"
        )
    validated: dict[str, dict[str, Any]] = {}
    for name, scheme in cast(Mapping[object, object], value).items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(
                "openapi_schema: security scheme names must be non-empty strings"
            )
        if not isinstance(scheme, Mapping):
            raise ConfigurationError(
                f"openapi_schema: security scheme {name!r} must be a mapping"
            )
        validated[name] = dict(cast(Mapping[str, Any], scheme))
    return validated


def _operation(
    item: Route,
    components: dict[str, Any],
    operation_ids: dict[str, int],
    error_schemas: list[dict[str, Any]],
) -> dict[str, Any]:
    declared = item.contract
    operation: dict[str, Any] = {
        "operationId": _unique_operation_id(item, operation_ids)
    }

    if declared.summary is not None:
        operation["summary"] = declared.summary
    description = declared.description or inspect.getdoc(item.use_case)
    if description:
        operation["description"] = description
    if declared.tags:
        operation["tags"] = list(declared.tags)
    if declared.deprecated:
        operation["deprecated"] = True
    if declared.sunset is not None:
        # Normalized to UTC so the extension and the Sunset header
        # describe the instant identically.
        operation["x-sunset"] = declared.sunset.astimezone(UTC).isoformat()
    if declared.timeout is not None:
        operation["x-timeout-seconds"] = declared.timeout

    parameters: list[dict[str, Any]] = []
    if declared.params is not None:
        parameters.extend(_parameters(declared.params, "path", components))
    if declared.query is not None:
        parameters.extend(_parameters(declared.query, "query", components))
    if declared.headers is not None:
        parameters.extend(_parameters(declared.headers, "header", components))
    if parameters:
        operation["parameters"] = parameters

    if declared.request is not None:
        operation["requestBody"] = {
            "required": True,
            "content": {
                declared.request_media_type: {
                    "schema": _json_schema(
                        declared.request, components, mode="validation"
                    )
                }
            },
        }

    operation["responses"] = _responses(declared, components, error_schemas)
    return operation


def _responses(
    declared: Contract[Any, Any],
    components: dict[str, Any],
    error_schemas: list[dict[str, Any]],
) -> dict[str, Any]:
    responses: dict[str, Any] = {}

    if declared.responses:
        for definition in declared.responses:
            responses[str(definition.status)] = _successful_response(
                declared,
                components,
                definition=definition,
            )
    else:
        responses[str(declared.status)] = _successful_response(declared, components)

    errors_by_status: dict[int, list[ErrorDef]] = {}
    for definition in declared.errors:
        errors_by_status.setdefault(definition.status, []).append(definition)

    has_validated_input = (
        declared.request is not None
        or declared.params is not None
        or declared.query is not None
        or declared.headers is not None
    )
    if has_validated_input:
        # The framework can return VALIDATION_ERROR regardless of what the
        # contract declares at 422, so merge rather than suppress.
        at_422 = errors_by_status.setdefault(tenchi_errors.validation_error.status, [])
        if tenchi_errors.validation_error not in at_422:
            at_422.append(tenchi_errors.validation_error)

    if declared.request is not None:
        # Request bodies are size-capped by default, so the framework's
        # 413 is part of the operation's honest surface.
        at_413 = errors_by_status.setdefault(tenchi_errors.request_too_large.status, [])
        if tenchi_errors.request_too_large not in at_413:
            at_413.append(tenchi_errors.request_too_large)

        at_415 = errors_by_status.setdefault(
            tenchi_errors.unsupported_media_type.status, []
        )
        if tenchi_errors.unsupported_media_type not in at_415:
            at_415.append(tenchi_errors.unsupported_media_type)

    if declared.timeout is not None:
        at_504 = errors_by_status.setdefault(tenchi_errors.request_timeout.status, [])
        if tenchi_errors.request_timeout not in at_504:
            at_504.append(tenchi_errors.request_timeout)

    for status, definitions in errors_by_status.items():
        responses[str(status)] = _error_response(definitions, error_schemas)

    return responses


def _successful_response(
    declared: Contract[Any, Any],
    components: dict[str, Any],
    *,
    definition: ResponseDef[Any, Any] | None = None,
) -> dict[str, Any]:
    response_type = definition.body if definition is not None else declared.response
    response_headers = (
        definition.headers if definition is not None else declared.response_headers
    )
    media_type = (
        definition.media_type
        if definition is not None
        else declared.response_media_type
    )
    response: dict[str, Any] = {
        "description": (
            definition.description if definition is not None else "Successful response"
        )
    }
    if response_type is not None:
        assert media_type is not None
        response["content"] = {
            media_type: {
                "schema": _json_schema(response_type, components, mode="serialization")
            }
        }
    headers = _successful_response_headers(
        declared,
        components,
        response_headers=response_headers,
    )
    if headers:
        response["headers"] = headers
    return response


def _successful_response_headers(
    declared: Contract[Any, Any],
    components: dict[str, Any],
    *,
    response_headers: Any,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if response_headers is not None:
        schema = _json_schema(response_headers, components, mode="serialization")
        object_schema = _resolved_object_schema(schema, components)
        if object_schema is None:
            type_name = getattr(response_headers, "__name__", repr(response_headers))
            raise ConfigurationError(
                f"openapi: response_headers type {type_name} must be object-shaped"
            )
        fields = _response_header_fields(
            object_schema,
            label=f"openapi: route {declared.name!r} response_headers",
            reference_root={"components": {"schemas": components}},
            validation_schema=TypeAdapter(response_headers).json_schema(
                mode="validation", by_alias=True
            ),
        )
        for _, name, property_schema, required in fields:
            headers[name] = {
                "required": required,
                "schema": dict(property_schema),
            }
    if declared.deprecated:
        headers["Deprecation"] = {
            "required": True,
            "schema": {"type": "string"},
        }
    if declared.sunset is not None:
        headers["Sunset"] = {
            "required": True,
            "schema": {"type": "string"},
        }
    return headers


def _error_response(
    definitions: list[ErrorDef], error_schemas: list[dict[str, Any]]
) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    error_schemas.append(schema)
    description = "; ".join(
        f"{definition.code}: {definition.message}" for definition in definitions
    )
    response: dict[str, Any] = {
        "description": description,
        "content": {"application/json": {"schema": schema}},
    }
    header_names: dict[str, str] = {}
    for definition in definitions:
        for name in definition.headers:
            header_names.setdefault(name.casefold(), name)
    if header_names:
        response["headers"] = {
            name: {"schema": {"type": "string"}} for name in header_names.values()
        }
    return response


def _error_component_name(components: dict[str, Any]) -> str:
    """Reserve a component for the framework envelope without hiding a user model."""
    suffix = 1
    while True:
        name = _ERROR_COMPONENT if suffix == 1 else f"{_ERROR_COMPONENT}_{suffix}"
        schema = {**_ERROR_SCHEMA, "title": name}
        existing = components.get(name)
        if existing is None:
            components[name] = schema
            return name
        if existing == schema:
            return name
        suffix += 1


def _parameters(
    annotation: Any,
    location: str,
    components: dict[str, Any],
) -> list[dict[str, Any]]:
    schema = _json_schema(annotation, components, mode="validation")
    object_schema = _resolved_object_schema(schema, components)
    if object_schema is None:
        type_name = getattr(annotation, "__name__", repr(annotation))
        raise ConfigurationError(
            f"openapi: {location} input type {type_name} must be object-shaped"
        )
    required = set(object_schema.get("required", []))
    parameters: list[dict[str, Any]] = []
    for name, property_schema in object_schema.get("properties", {}).items():
        parameters.append(
            {
                "name": name.replace("_", "-") if location == "header" else name,
                "in": location,
                "required": location == "path" or name in required,
                "schema": property_schema,
            }
        )
    return parameters


def _resolved_object_schema(
    schema: Mapping[str, Any], components: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    object_schema = _object_schema(schema)
    reference = schema.get("$ref")
    component_prefix = "#/components/schemas/"
    if (
        object_schema is None
        and isinstance(reference, str)
        and reference.startswith(component_prefix)
    ):
        component = components.get(reference.removeprefix(component_prefix))
        if isinstance(component, Mapping):
            object_schema = _object_schema(cast(Mapping[str, Any], component))
    return object_schema


def _json_schema(
    annotation: Any,
    components: dict[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    schema = TypeAdapter(annotation).json_schema(
        mode="serialization" if mode == "serialization" else "validation",
        by_alias=True,
        ref_template="#/components/schemas/{model}",
    )
    for name, definition in schema.pop("$defs", {}).items():
        existing = components.get(name)
        if existing is not None and existing != definition:
            # Same component name, different schema: either two distinct
            # models share a class name, or one model's validation and
            # serialization schemas diverge (computed fields, validation
            # aliases). Refusing beats silently documenting the wrong one.
            raise ConfigurationError(
                f"openapi: conflicting schemas for component {name!r}; "
                "rename one of the models so both can be documented"
            )
        components.setdefault(name, definition)
    return schema


def _unique_operation_id(item: Route, operation_ids: dict[str, int]) -> str:
    base = getattr(item.use_case, "__name__", None) or "operation"
    count = operation_ids.get(base, 0)
    operation_ids[base] = count + 1
    return base if count == 0 else f"{base}_{count + 1}"
