# Tenchi

Tenchi is a small, contract-first Python framework for building typed HTTP
APIs. Contracts define the boundary, plain async functions implement use cases,
and frozen dataclasses carry explicitly wired dependencies.

Tenchi uses Pydantic for validation, Starlette for ASGI, and httpx for its typed
client. It requires Python 3.12 or newer and is currently pre-1.0.

Read the [one-page guide](https://taylorbryant.github.io/tenchi/) for examples
of contracts, use cases, application wiring, errors, the typed client, workers,
pagination, testing, and the CLI.

## Quick start

Create and run a working application:

```sh
uvx tenchi new my_app
cd my_app
uv sync
uv run tenchi dev
```

The generated app includes a todos feature, an in-memory adapter, tests, and
explicit server wiring. With the server running:

```sh
curl -X POST http://127.0.0.1:8000/todos \
  -H 'content-type: application/json' \
  -d '{"title": "Buy milk"}'
```

To add Tenchi to an existing project instead:

```sh
uv add tenchi
```

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
  features/<feature>/   # contracts, schemas, ports, routes, use cases
  shared/               # shared errors and domain concepts
  infra/                # concrete port implementations
  server/               # context, hooks, route composition, ASGI app
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
- A contract-driven async client and OpenAPI 3.1 generation.
- Lifespan resources, request-scoped contexts, authentication hooks, middleware,
  request deadlines, outcome observers, pagination, health checks, and
  in-process testing helpers.

Contracts are private by default. Set `public=True` for operations that an
authentication hook should exempt, then inspect the same metadata in the hook:

```python
health = contract(method="GET", path="/health", response=Health, public=True)


def authenticate(info: RequestInfo, context: AppContext) -> AppContext | None:
    if info.contract.public:
        return None
    # Authenticate and return an enriched context, or raise AppError.
```

When OpenAPI security schemes are configured, public operations receive an
empty per-operation security requirement. `health_route()` and
`openapi_route()` are public by default; pass `public=False` to protect them.
The metadata itself does not authenticate requests—application hooks remain in
control.

For endpoints with more than one successful status, declare named outcomes and
select one in a synchronous presenter. The same mechanism is Tenchi's
controlled HTTP escape hatch: a passthrough outcome may return a Starlette
`StreamingResponse`, `FileResponse`, or redirect while its status, media type,
media-type parameters, and headers remain contract-owned. No-body outcomes
accept only a concrete response with an empty materialized body; streaming
outcomes must declare their body type. The typed client reports the selected
outcome on `ClientResponse.success` and validates its declared body and headers.

```python
from tenchi.responses import PresentedResponse, present, success

created = success(name="created", status=201, response=Todo)
existing = success(name="existing", status=200, response=Todo)

put_todo_contract = contract(
    method="PUT",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    successes=(created, existing),
    timeout=5.0,
)

def present_put(result: PutTodoResult) -> PresentedResponse:
    outcome = created if result.created else existing
    return present(outcome, body=result.todo)

route(put_todo_contract, put_todo, present=present_put)
```

`timeout=` cooperatively cancels overdue work, lets request-scope cleanup and
rollback finish, then returns the framework's 504 even if application code
catches the injected cancellation. `create_app(observers=...)` delivers an
immutable `RequestOutcome`, including a read-only header mapping, after each
matched route has finalized. Observer failures are logged and never change the
response.

See [`examples/todos`](examples/todos) for the small teaching app and
[`examples/taskboard`](examples/taskboard) for a larger application with
authentication, authorization, SQLite transactions, optimistic concurrency
through `ETag` / `If-Match`, idempotent task creation, multiple successful
outcomes, request observation, deadlines, and background work.

## CLI

```sh
tenchi new my_app
tenchi make feature notes
tenchi make use-case notes create_note
tenchi routes
tenchi openapi
tenchi openapi --diff openapi.json
tenchi openapi --check openapi.json
tenchi openapi --write openapi.json
tenchi doctor
tenchi dev
```

`openapi --write` stores canonical, key-sorted JSON. Before accepting a changed
snapshot, run `openapi --diff` to classify changes as breaking, additive,
metadata-only, or unknown. Breaking and unknown changes return a non-zero
status; additive and metadata-only changes pass. Use `--diff-format json` for
machine-readable output. `openapi --check` remains the exact drift check for
tests and CI. Pass the same `--routes`, `--title`, `--version`, `--description`,
and `--security` options in every command when your document uses them. Run
`--diff` before replacing the baseline with `--write`; in CI, compare
against the snapshot from the merge base or previous release rather than the
snapshot committed in the same change. `--output` and `-o` remain aliases for
`--write`. For programmatic checks, import `analyze_openapi_compatibility` from
`tenchi.compatibility`.

Run `tenchi <command> --help` for command options.

## Development

```sh
uv sync
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
```

Tenchi is licensed under the MIT License.
