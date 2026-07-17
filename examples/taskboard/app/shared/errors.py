from tenchi.errors import ErrorDef

unauthorized = ErrorDef(
    code="UNAUTHORIZED",
    status=401,
    message="Unauthorized",
)

forbidden = ErrorDef(
    code="FORBIDDEN",
    status=403,
    message="You do not have access to this resource",
)

project_not_found = ErrorDef(
    code="PROJECT_NOT_FOUND",
    status=404,
    message="Project not found",
)

task_not_found = ErrorDef(
    code="TASK_NOT_FOUND",
    status=404,
    message="Task not found",
)

idempotency_conflict = ErrorDef(
    code="IDEMPOTENCY_CONFLICT",
    status=409,
    message="The idempotency key was already used with different input",
)

precondition_required = ErrorDef(
    code="PRECONDITION_REQUIRED",
    status=428,
    message="An If-Match header is required",
)

precondition_failed = ErrorDef(
    code="PRECONDITION_FAILED",
    status=412,
    message="The resource has changed since it was read",
)
