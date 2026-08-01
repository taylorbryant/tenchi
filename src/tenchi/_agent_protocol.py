"""Runtime contracts for structured CLI results consumed by agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from ._app_map import AppMapPayload
from ._cli_results import (
    CheckPayload,
    DoctorPayload,
    EvaluationListPayload,
    EvaluationRunPayload,
    MakePayload,
    PreflightPayload,
    RoutesPayload,
    TaskListPayload,
    TaskRunPayload,
)
from ._openapi_operations import OpenApiDiffPayload
from ._tool_operations import ToolDiffPayload, ToolListPayload
from ._verify_operations import VerificationPayload

type AgentResultName = Literal[
    "app_map",
    "routes",
    "doctor",
    "evaluation_list",
    "evaluation_run",
    "openapi_diff",
    "make",
    "check",
    "preflight",
    "task_list",
    "task_run",
    "tool_list",
    "tool_diff",
    "verify",
]
type JsonObject = dict[str, object]

AGENT_RESULT_ADAPTERS: dict[AgentResultName, TypeAdapter[object]] = {
    "app_map": cast(TypeAdapter[object], TypeAdapter(AppMapPayload)),
    "routes": cast(TypeAdapter[object], TypeAdapter(RoutesPayload)),
    "doctor": cast(TypeAdapter[object], TypeAdapter(DoctorPayload)),
    "evaluation_list": cast(
        TypeAdapter[object],
        TypeAdapter(EvaluationListPayload),
    ),
    "evaluation_run": cast(
        TypeAdapter[object],
        TypeAdapter(EvaluationRunPayload),
    ),
    "openapi_diff": cast(TypeAdapter[object], TypeAdapter(OpenApiDiffPayload)),
    "make": cast(TypeAdapter[object], TypeAdapter(MakePayload)),
    "check": cast(TypeAdapter[object], TypeAdapter(CheckPayload)),
    "preflight": cast(TypeAdapter[object], TypeAdapter(PreflightPayload)),
    "task_list": cast(TypeAdapter[object], TypeAdapter(TaskListPayload)),
    "task_run": cast(TypeAdapter[object], TypeAdapter(TaskRunPayload)),
    "tool_list": cast(TypeAdapter[object], TypeAdapter(ToolListPayload)),
    "tool_diff": cast(TypeAdapter[object], TypeAdapter(ToolDiffPayload)),
    "verify": cast(TypeAdapter[object], TypeAdapter(VerificationPayload)),
}
AGENT_RESULT_NAMES: tuple[AgentResultName, ...] = tuple(AGENT_RESULT_ADAPTERS)


def agent_result_schemas() -> dict[AgentResultName, JsonObject]:
    """Return the serialization schema owned by every registered result name."""
    return {
        name: cast(
            JsonObject,
            adapter.json_schema(mode="serialization"),
        )
        for name, adapter in AGENT_RESULT_ADAPTERS.items()
    }


def validate_agent_result(
    name: AgentResultName,
    value: Mapping[str, object],
) -> JsonObject:
    """Validate an exact structured result before it crosses the CLI boundary."""
    raw = dict(value)
    try:
        validated = AGENT_RESULT_ADAPTERS[name].validate_python(raw, strict=True)
    except ValidationError as exc:
        raise ValueError(
            f"Agent result {name!r} does not match its registered schema: {exc}"
        ) from exc

    if not isinstance(validated, dict):
        raise ValueError(f"Agent result {name!r} did not validate as an object")
    mapping = cast(dict[object, object], validated)
    if not all(isinstance(key, str) for key in mapping):
        raise ValueError(f"Agent result {name!r} contains a non-string key")
    result = cast(JsonObject, mapping)
    if result != raw:
        raise ValueError(
            f"Agent result {name!r} does not exactly match its registered schema"
        )
    return result
