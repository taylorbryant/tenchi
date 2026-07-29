from app.shared.errors import (
    forbidden,
    precondition_failed,
    task_not_found,
    unauthorized,
)
from tenchi.idempotency import IDEMPOTENCY_CONFLICT, IDEMPOTENCY_IN_PROGRESS
from tenchi.rate_limits import RATE_LIMITED
from tenchi.tools import tool, tool_group, tool_handler

from .schemas import MoveTaskInput, Task
from .use_cases.move_task import move_task

move_task_tool = tool(
    "tasks.move",
    request=MoveTaskInput,
    result=Task,
    description="Move one visible task to another workflow status.",
    errors=(
        unauthorized,
        task_not_found,
        forbidden,
        precondition_failed,
        IDEMPOTENCY_CONFLICT,
        IDEMPOTENCY_IN_PROGRESS,
        RATE_LIMITED,
    ),
    destructive=True,
    idempotent=True,
    open_world=False,
)

tools = tool_group(
    tool_handler(move_task_tool, move_task),
)
