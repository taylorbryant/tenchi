from tenchi.errors import ErrorDef

unauthorized = ErrorDef(
    code="UNAUTHORIZED",
    status=401,
    message="Unauthorized",
)

source_not_found = ErrorDef(
    code="SOURCE_NOT_FOUND",
    status=404,
    message="Source not found",
)

answer_generation_failed = ErrorDef(
    code="ANSWER_GENERATION_FAILED",
    status=502,
    message="The answer provider returned an invalid result",
)

answer_generation_timed_out = ErrorDef(
    code="ANSWER_GENERATION_TIMED_OUT",
    status=504,
    message="The answer provider did not respond before the deadline",
)
