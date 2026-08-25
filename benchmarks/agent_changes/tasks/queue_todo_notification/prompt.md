# Queue a durable todo-created job

Creating a todo must persist a validated background message for later delivery.
Add a `TodoCreated` Pydantic message containing `todo_id` and `title`, and
declare the stable job name `todos.created`. Bind it to an async consumer at
server composition so the job is discoverable and dispatchable. The consumer
may acknowledge the message without an external side effect; the queue worker
and notification provider are deliberately outside this task.

The starting application includes an `Outbox` port and a `SqliteOutbox`
adapter. Extend `AppContext` and compose the outbox with the todo repository on
the same request-scoped SQLite connection. In `create_todo`, serialize the
message with `job_message()` and enqueue its stable name and exact
`payload_json` after creating the todo. The todo and outbox row must commit or
roll back as one unit. Provide a memory outbox for direct use-case tests.

Add focused producer and consumer tests. Inspect job compatibility before
accepting the updated `jobs.json` snapshot. Do not implement a scheduler,
retry loop, or queue runtime.

Use the repository's `AGENTS.md` workflow and finish with a passing
`uv run tenchi verify --base-ref HEAD`.
