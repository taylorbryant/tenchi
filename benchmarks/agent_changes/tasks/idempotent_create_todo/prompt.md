# Make todo creation retry-safe

Clients can lose the response to `POST /todos` and retry. The starting API
already requires a non-empty `Idempotency-Key` request header (maximum 128
characters), passes the validated header to the use case, and declares the
standard conflict and in-progress errors. That published protocol prepares
clients for this change, but the application does not yet enforce it. Make
create a durably idempotent command.

The starting application includes a `SqliteIdempotencyStore` adapter designed
to share a request's SQLite transaction. Compose it with the todo repository on
one connection and expose it through `AppContext`. In the create use case, use
Tenchi's `fingerprint()` and `run_idempotently()` with namespace
`todos.create`, a stable application scope, the validated header key,
`CreateTodo` as the fingerprint annotation, and `Todo` as the result type.

Repeating a key with the same request must return the original response across
application restarts without creating a second todo. Reusing it for different
validated input must return `IDEMPOTENCY_CONFLICT`; in-progress work must use
Tenchi's standard declared error. Mark the contract with `idempotency_key=True`.
The idempotency record and todo insert must commit or roll back together.

Update direct and HTTP tests, the contract examples if needed, and OpenAPI.
Inspect compatibility before accepting the snapshot.

Use the repository's `AGENTS.md` workflow and finish with a passing
`uv run tenchi verify --base-ref HEAD`.
