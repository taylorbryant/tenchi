# Tenchi

Tenchi is a small, contract-first Python framework for building typed HTTP
APIs. Contracts define the boundary, plain async functions implement use cases,
and frozen dataclasses carry explicitly wired dependencies.

Tenchi uses Pydantic for validation, Starlette for ASGI, and httpx for its typed
client. It requires Python 3.12 or newer and is currently pre-1.0.

Read the [documentation](https://tenchi.io/) for the
quickstart, mental model, complete contract and runtime guides, production
workflow, comparisons, and module reference.

## Quick start

Create and run a working application:

```sh
uvx tenchi new my_app
cd my_app
uv sync
uv run tenchi dev
```

The generated app includes a todos feature, SQLite persistence, memory-backed
unit tests, Swagger UI, health and OpenAPI routes, and a CI compatibility gate.
With the server running, open `http://127.0.0.1:8000/docs` or call it directly:

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
- A contract-driven async client, OpenAPI 3.1 generation, and optional Swagger
  UI route.
- Lifespan resources, request-scoped contexts, authentication hooks, middleware,
  request deadlines, outcome observers, pagination, health checks, and
  in-process testing helpers.

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
tenchi openapi --diff-ref origin/main --snapshot openapi.json
tenchi openapi --check openapi.json
tenchi openapi --write openapi.json
tenchi doctor
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
