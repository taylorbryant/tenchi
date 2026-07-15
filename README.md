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
create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
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
routes = route_group(
    route(create_todo_contract, create_todo),
)
```

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
  headers, and responses; field aliases are the names used on the wire and in
  OpenAPI, and nullable request types can send JSON `null` explicitly.
- `typing.Protocol` ports and explicit dependency wiring instead of a DI
  container.
- Declared application errors with a stable JSON envelope.
- A named exception hierarchy that distinguishes configuration mistakes from
  runtime application and transport failures.
- A contract-driven async client and OpenAPI 3.1 generation.
- Lifespan resources, request-scoped contexts, authentication hooks, middleware,
  pagination, health checks, and in-process testing helpers.

See [`examples/todos`](examples/todos) for the small teaching app and
[`examples/taskboard`](examples/taskboard) for a larger application with
authentication, authorization, SQLite transactions, and background work.

## CLI

```sh
tenchi new my_app
tenchi make feature notes
tenchi make use-case notes create_note
tenchi routes
tenchi openapi
tenchi doctor
tenchi dev
```

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
