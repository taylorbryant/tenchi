# Tenchi

Tenchi is a small, contract-first Python framework for building typed
backends. HTTP contracts and application tools define validated boundaries,
plain async functions implement use cases, and frozen dataclasses carry
explicitly wired dependencies.

Tenchi uses Pydantic for validation, Starlette for ASGI, and httpx for its typed
client. It requires Python 3.12 or newer and is currently pre-1.0.

Read the [documentation](https://tenchi.io/) for the
quickstart, mental model, complete contract and runtime guides,
[production handbook](https://tenchi.io/production), comparisons, and module
reference.

## Quick start

Create and run a working application:

```sh
uvx tenchi new my_app
cd my_app
uv sync
uv run tenchi check
uv run tenchi dev
```

The generated app includes a todos feature, SQLite persistence, memory-backed
unit tests, Swagger UI, health and OpenAPI routes, an agent guide, and a CI
compatibility gate. It also includes `.mcp.json`, so MCP-aware coding agents can
use Tenchi's structured inspection and validation tools after `uv sync`. With
the server running, open
`http://127.0.0.1:8000/docs` or call it directly:

```sh
curl -X POST http://127.0.0.1:8000/todos \
  -H 'content-type: application/json' \
  -d '{"title": "Buy milk"}'
```

To add Tenchi to an existing project instead:

```sh
uv add tenchi
```

Add MCP support with `uv add --dev "tenchi[mcp]"` and follow the [MCP server
guide](https://tenchi.io/mcp) to connect a coding agent.

Add the payload-safe OpenTelemetry bridge with
`uv add "tenchi[otel]" opentelemetry-sdk`. Tenchi records finalized request,
use-case, client, and retry-attempt outcomes through the providers your
application configures; see the
[observability guide](https://tenchi.io/observability).

Follow the [existing-project guide](https://tenchi.io/existing-project) to add
the first contract, use case, ASGI application, test, and OpenAPI baseline.

## How it works

A contract declares the HTTP boundary:

```python
class CreatedTodoHeaders(BaseModel):
    location: str = Field(alias="Location")


create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    response_headers=CreatedTodoHeaders,
    status=201,
)
```

A use case is an ordinary async function whose dependencies come from the app
context:

```python
async def create_todo(request: CreateTodo, context: AppContext) -> Todo:
    return await context.todos.create(title=request.title)
```

A route binds them together. Tenchi immediately checks that every boundary
parameter and the return annotation exactly match the contract, so invalid
wiring fails during application composition rather than on a request:

```python
def create_todo_headers(todo: Todo) -> CreatedTodoHeaders:
    return CreatedTodoHeaders(Location=f"/todos/{todo.id}")


routes = route_group(
    route(
        create_todo_contract,
        create_todo,
        response_headers=create_todo_headers,
    ),
)
```

The synchronous response-header projector keeps HTTP metadata at the route
boundary while the use case continues to return only domain data. Tenchi
validates and serializes those headers before the request scope commits. The
typed client validates them on every call; use `call_with_response()` when you
also want the typed headers and underlying httpx response.

Applications use this structure:

```text
app/
  features/<feature>/   # contracts, schemas, ports, routes, jobs, tasks, tools, use cases
  shared/               # shared errors and domain concepts
  infra/                # concrete port implementations
  server/               # context, runtime, preflight, route/job/task/tool composition, ASGI app
tests/                  # HTTP integration tests
```

The main pieces are:

- Pydantic validation for request bodies, path parameters, query parameters,
  request and successful response headers, and response bodies; field aliases
  are the names used on the wire and in OpenAPI, and nullable request types can
  send JSON `null` explicitly. Declared media types are enforced against wire
  `Content-Type`: mismatched requests receive a framework-owned 415 and the
  typed client rejects mismatched responses. Charset-qualified text contracts
  are encoded and decoded strictly in both directions; unsupported declared
  charsets fail when the contract is built.
- `typing.Protocol` ports and explicit dependency wiring instead of a DI
  container.
- Declared application errors with a stable JSON envelope.
- A named exception hierarchy that distinguishes configuration mistakes from
  runtime application and transport failures.
- A contract-driven async client with explicit bounded retries, per-attempt
  outcomes, OpenAPI 3.1 generation, and an optional Swagger UI route.
- Lifespan resources, request-scoped contexts, authentication hooks, middleware,
  request deadlines, HTTP and use-case outcome observers, pagination, health
  checks, in-process testing helpers, and an optional OpenTelemetry bridge for
  logical spans and bounded-cardinality metrics.
- Infrastructure-neutral idempotency with canonical validated-input
  fingerprints, durable store transitions, typed result replay, scoped keys,
  expiration, standard conflict/in-progress errors, and a concurrency-safe
  memory adapter for tests.
- Infrastructure-neutral fixed-window rate limits with atomic store decisions,
  weighted costs, a standard declared 429 response, and a concurrency-safe
  memory adapter for tests.
- Reusable idempotency and rate-limit adapter conformance checks for
  persistence, identity isolation, expiration boundaries, token fencing, and
  concurrent admission.
- Signed inbound webhook bindings that verify exact, size-bounded bytes before
  parsing, attach service identity to the request context, and fail composition
  when a marked webhook contract has no verifier.
- Queue-neutral background jobs with validated producer messages,
  composition-time handler bindings, validated consumer results, shared
  use-case outcomes, and application-map nodes. Applications keep ownership of
  queue persistence, acknowledgement, retries, and dead letters.

`public` defaults to `False`. Set `public=True` for operations that an
authentication hook should exempt, then inspect the metadata in the hook:

```python
health = contract(method="GET", path="/health", response=Health, public=True)


def authenticate(info: RequestInfo, context: AppContext) -> AppContext | None:
    if info.contract.public:
        return None
    # Authenticate and return an enriched context, or raise AppError.
```

When OpenAPI security schemes are configured, public operations receive an
empty per-operation security requirement. `health_route()` and
`openapi_route()` and `swagger_ui_route()` are public by default; pass
`public=False` to protect them.
The metadata itself does not authenticate requests—application hooks remain in
control.

Signed webhook contracts use a separate exact-body authentication boundary:

```python
delivery = contract(
    method="POST",
    path="/webhooks/delivery",
    request=Delivery,
    status=204,
    public=True,
    webhook=True,
)

app = create_app(
    routes=route_group(route(delivery, receive_delivery)),
    context_factory=create_context,
    webhooks=(webhook(delivery, verify_delivery),),
)
```

The verifier sees the immutable wire bytes and read-only headers before
Pydantic parses the body. It may return an enriched context carrying verified
service identity. See the [signed webhook
guide](https://tenchi.io/webhooks) for HMAC verification, replay protection,
and error declarations.

Application-level quotas stay in use cases and use authenticated scope:

```python
permit = await enforce_rate_limit(
    context.rate_limits,
    namespace="tasks.create",
    scope=user.id,
    limit=5,
    window_seconds=60,
)
```

Exhausted windows raise the declared `RATE_LIMITED` error with `Retry-After`.
Use the built-in `MemoryRateLimitStore` in tests and supply an atomic shared
store in multi-process deployments. See [rate limiting](https://tenchi.io/rate-limits)
for idempotency placement, transaction choices, and edge-limit boundaries.

For endpoints with more than one successful status, declare response
definitions and select one in a synchronous presenter. Their body and header
types become the contract's aggregate typed-client result, so they are the
only source of truth. The same mechanism is Tenchi's controlled HTTP escape
hatch: a passthrough definition may return a Starlette
`StreamingResponse`, `FileResponse`, or redirect while its status, media type,
media-type parameters, and headers remain contract-owned. No-body definitions
accept only a concrete response with an empty materialized body; streaming
definitions must declare their body type. The typed client reports the selected
definition on `ClientResponse.definition` and validates its body and headers.

```python
from tenchi.responses import PresentedResponse, present, response

created = response(Todo, status=201)
existing = response(Todo, status=200)

put_todo_contract = contract(
    method="PUT",
    path="/todos",
    request=CreateTodo,
    responses=(created, existing),
    timeout=5.0,
)

def present_put(result: PutTodoResult) -> PresentedResponse:
    outcome = created if result.created else existing
    return present(outcome, result.todo)

route(put_todo_contract, put_todo, present=present_put)
```

When one status permits alternative top-level body schemas, pass them as
separate positional alternatives so Pyright preserves the exact union:

```python
flexible = response(Todo, str, status=200)  # ResponseDef[Todo | str, None]
```

Nested unions retain their ordinary spelling, such as
`response(list[Todo | str], status=200)`. Each response definition has one
fixed object-shaped header schema; differing header shapes belong in separate
definitions.

`timeout=` cooperatively cancels overdue work, lets request-scope cleanup and
rollback finish, then returns the framework's 504 even if application code
catches the injected cancellation. `create_app(observers=...)` delivers an
immutable `RequestOutcome`, including a read-only header mapping, after each
matched route has finalized. Observer failures are logged and never change the
response. `create_app(use_case_observers=...)` and
`execute(use_case_observers=...)`, task runners, and tool runners deliver the
same immutable `UseCaseOutcome` after context cleanup for every use case that
was actually invoked. Finalized outcomes include a UTC `completed_at` captured
before observer delivery, so delayed observers do not move the event's
timestamp.
`Client(observers=...)` delivers immutable `ClientOutcome` values for outbound
contract calls, including transport failures, invalid responses, and
cancellation, without carrying input, URL, header, body, or exception payloads.

Named operational tasks add validated input and result contracts plus
application lifecycle wiring around selected use cases. Use them for
operator-invoked backfills, repairs, replays, and maintenance—not scheduling or
queue consumption:

```python
repair_members_task = task(
    "projects.repair_members",
    repair_project_members,
    description="Replace malformed project member lists.",
)
```

Application tools expose selected use cases to AI and other machine callers
without choosing a model provider or transport:

```python
search_projects_tool = tool(
    "projects.search",
    result=list[Project],
    description="List projects owned by the authenticated user.",
    errors=(unauthorized,),
    read_only=True,
    open_world=False,
)

tools = tool_group(
    tool_handler(search_projects_tool, list_projects),
)
```

`ToolRunner` validates input before application resources open, validates the
result and its serialized form before context commit, preserves only declared
application errors, and masks unexpected failures. `tool_manifest()` returns
deterministic input and output JSON Schemas plus safety metadata for a transport
adapter; `TOOL_MANIFEST_VERSION` identifies that guarded protocol. See the
[application tools guide](https://tenchi.io/tools).

See [`examples/todos`](examples/todos) for the small teaching app and
[`examples/taskboard`](examples/taskboard) for a larger application with
authentication, authorization, SQLite transactions, optimistic concurrency
through `ETag` / `If-Match`, idempotent task creation, multiple successful
outcomes, request observation, deadlines, background work, and authenticated
application tools.

## CLI

```sh
tenchi new my_app
tenchi make feature notes --dry-run
tenchi make feature notes --json
tenchi make use-case notes create_note --dry-run
tenchi routes
tenchi map
tenchi map --feature notes --kind route,use-case,port --json
tenchi openapi
tenchi openapi --diff openapi.json
tenchi openapi --diff-ref origin/main --snapshot openapi.json
tenchi openapi --check openapi.json
tenchi openapi --write openapi.json
tenchi doctor --json
tenchi check
tenchi preflight
tenchi preflight --json
tenchi task list
tenchi task run projects.repair_members --input '{"dry_run": true}'
tenchi mcp
tenchi dev
```

`openapi --write` stores canonical, key-sorted JSON. Before accepting a changed
snapshot, run `openapi --diff` to classify changes as breaking, additive,
metadata-only, or unknown. Breaking and unknown changes return a non-zero
status; additive and metadata-only changes pass. Use `--diff-format json` for
machine-readable output. `--diff-ref` reads the snapshot at a Git commit, which
keeps a pull-request gate historical even when the branch updates its snapshot.
`openapi --check` remains the exact drift check for tests and CI. Pass the same
`--routes`, `--title`, `--version`, `--description`, and `--security` options in
every command when your document uses them. Run `--diff` before replacing the
baseline with `--write`. `--output` and `-o` remain aliases for `--write`. For
programmatic checks, import `analyze_openapi_compatibility` from
`tenchi.compatibility`.

`tenchi map` combines source declarations with the composed route group into a
deterministic graph of features, contracts, routes, operational tasks, use
cases, policies, ports, adapters, context, entrypoints, and tests. Every
relationship includes source evidence and a confidence level. Use `--feature`
for a feature plus its direct cross-feature dependencies, `--kind` for a
comma-separated node projection, and `--json` for the versioned result.

`tenchi task list` reports the runner's task names and JSON Schemas. `tenchi
task run` validates input, owns one application lifespan and scoped context,
and validates output before transactional cleanup commits. Both commands
support versioned JSON results.

`tenchi preflight` runs the read-only, timeout-bounded observations declared by
`app.server.preflight:checks` against the selected deployment environment. It
is deliberately separate from deterministic `tenchi check`; use it after
migrations and before traffic shifts to verify database connectivity and
schema compatibility, secret-manager access, outbound dependencies, and worker
readiness. Structured results expose only static names, descriptions, failure
codes, statuses, and durations. See the [deployment preflight
guide](https://tenchi.io/preflight).

Generator `--dry-run` output lists every file without writing it. `make`,
`map`, `doctor`, and `check` accept `--json` and return versioned results for
agents and automation. `tenchi check` runs Ruff formatting and linting,
Pyright, pytest, doctor, and the OpenAPI snapshot check even when an earlier
step fails; failed output is bounded and each step reports its duration.
Tenchi snapshots the JSON Schema for these results and the MCP tool surface;
breaking protocol changes require a new `schema_version`.
See the [coding-agent workflow](https://tenchi.io/agents) for the complete
inspect, preview, edit, validate, and compatibility loop.

`tenchi mcp` serves the same versioned map, route, preflight, task-discovery,
doctor, generator-preview, OpenAPI-diff, and check results over stdio. Task
execution is absent unless the server starts with `--allow-task-runs`.
Preflight remains available as a read-only tool but deliberately contacts the
environment captured by the server process. Inspection and preview tools do
not write application files; project-owned commands executed by `check` retain
their normal side effects. Generated apps register the command in `.mcp.json`.
Existing apps install the optional development dependency with
`uv add --dev "tenchi[mcp]"`.

Run `tenchi <command> --help` for command options.

## Development

```sh
uv sync
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
```

The documentation is a separate Bun and Next.js application:

```sh
cd docs
bun install
bun run dev
```

Run `bun run check` in `docs/` to lint, type-check, test, and build the static
GitHub Pages export. Search data and `llms.txt` files are generated during the
build from the registered MDX pages.

Tenchi is licensed under the MIT License.
