# Tenchi

Tenchi is a contract-first, Python-native framework for building REST APIs
around use cases, ports, and explicit dependency wiring. It is the Python
sibling of Beignet: the same architecture — contracts at the HTTP boundary,
use cases at the center, protocol-based ports, infrastructure adapters, and
explicit server composition — expressed with plain functions, dataclasses,
`typing.Protocol`, Pydantic v2, and Starlette instead of TypeScript
machinery.

## Installation

Tenchi requires Python 3.12+.

```sh
uv add tenchi          # or: pip install tenchi
```

To work on this repository:

```sh
uv sync                # install the package and dev tools
uv run pytest          # tests (framework + todos example)
uv run ruff check .    # lint
uv run pyright         # strict type checking
```

## Architecture

Applications follow a prescriptive structure. Each feature owns its
contracts, schemas, ports, routes, use cases, and tests; infrastructure
implements ports; server composition owns concrete wiring:

```txt
app/
  features/
    todos/
      contracts.py        # HTTP boundary: method, path, request/response, errors
      schemas.py          # Pydantic models shared by contracts, use cases, ports
      ports.py            # typing.Protocol interfaces the feature needs
      routes.py           # binds contracts to use cases
      use_cases/          # application workflows (plain async functions)
      tests/              # use-case tests, no HTTP required
  shared/
    errors.py             # application error definitions with stable codes
  infra/
    memory_todo_repository.py   # concrete port implementations
    port_wiring.py              # constructs concrete adapters
  server/
    context.py            # AppContext dataclass holding ports
    routes.py             # composes feature route groups
    asgi.py               # concrete wiring + ASGI app
tests/                    # HTTP integration tests
```

Dependency direction is strict: schemas and use cases never import
infrastructure or the HTTP runtime; routes bind contracts to use cases but
construct nothing concrete; only `server/` (and `infra/`) know which
implementations are in play.

## The basic flow

Schemas are ordinary Pydantic models:

```python
# app/features/todos/schemas.py
from pydantic import BaseModel

class CreateTodo(BaseModel):
    title: str

class Todo(BaseModel):
    id: str
    title: str
    completed: bool
```

Ports describe what application code needs, as protocols:

```python
# app/features/todos/ports.py
from typing import Protocol
from .schemas import Todo

class TodoRepository(Protocol):
    async def create(self, *, title: str) -> Todo: ...
    async def list(self) -> list[Todo]: ...
```

The application context is a frozen dataclass of ports:

```python
# app/server/context.py
from dataclasses import dataclass
from app.features.todos.ports import TodoRepository

@dataclass(frozen=True, slots=True)
class AppContext:
    todos: TodoRepository
```

Use cases are plain async functions — no base classes, no decorators:

```python
# app/features/todos/use_cases/create_todo.py
from app.server.context import AppContext
from ..schemas import CreateTodo, Todo

async def create_todo(request: CreateTodo, context: AppContext) -> Todo:
    return await context.todos.create(title=request.title)
```

Contracts define and validate the HTTP boundary. Any type Pydantic can
validate works, including `list[Todo]`:

```python
# app/features/todos/contracts.py
from tenchi.contracts import contract
from .schemas import CreateTodo, Todo

create_todo_contract = contract(
    method="POST",
    path="/todos",
    request=CreateTodo,
    response=Todo,
    status=201,
)
```

Contracts can also carry documentation metadata (`summary=`,
`description=`, `tags=`, `deprecated=`) and non-JSON media types: pair
`request_media_type="text/plain"` with `request=str`, or
`"application/octet-stream"` with `bytes`, and the server, client, and
OpenAPI document all follow (useful for webhook endpoints that need the
raw body).

Contracts can also declare path parameters (`params=`), query parameters
(`query=`), and request headers (`headers=`), each validated into its own
model and passed to the use case as a keyword argument of the same name.
Header names map to fields by lowercasing and swapping `-` for `_`
(`X-Api-Key` → `x_api_key`); the client and OpenAPI document reverse the
mapping. For example:

```python
class ListTodosQuery(BaseModel):
    completed: bool | None = None

list_todos_contract = contract(
    method="GET",
    path="/todos",
    query=ListTodosQuery,
    response=list[Todo],
)

async def list_todos(query: ListTodosQuery, context: AppContext) -> list[Todo]:
    ...
```

Routes bind contracts to use cases. Binding is validated eagerly, so a use
case that cannot accept what its contract declares fails at import time:

```python
# app/features/todos/routes.py
from tenchi.routes import route, route_group
from .contracts import create_todo_contract
from .use_cases.create_todo import create_todo

routes = route_group(
    route(create_todo_contract, create_todo),
)
```

Server composition owns concrete wiring and produces the ASGI app. The
lifespan owns process-scoped resources — it opens them at startup, closes
them at shutdown, and whatever it yields is handed to the context factory,
which runs once per request:

```python
# app/server/asgi.py
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from tenchi.server import create_app
from app.features.todos.ports import TodoRepository
from app.infra.port_wiring import open_todo_repository
from app.server.context import AppContext
from app.server.routes import routes

@asynccontextmanager
async def lifespan() -> AsyncGenerator[TodoRepository]:
    async with open_todo_repository("todos.db") as todos:
        yield todos

def create_context(todos: TodoRepository) -> AppContext:
    return AppContext(todos=todos)

app = create_app(routes=routes, context_factory=create_context, lifespan=lifespan)
```

For apps without real resources, `lifespan` is optional and the context
factory can take zero arguments and close over module-scoped objects (see
the memory-backed fixtures in the example tests).

The context factory may itself be an async context manager — then it is
entered at request start and exited at request end, and a use-case or
hook exception flows through `__aexit__` before the error response is
built. That is the home for a per-request unit of work: commit on
success, roll back on error.

```python
@asynccontextmanager
async def create_context(pool: Pool) -> AsyncGenerator[AppContext]:
    async with pool.connection() as conn, conn.transaction():
        yield AppContext(todos=SqlTodoRepository(conn))
```

The taskboard example wires exactly this with SQLite (see
`examples/taskboard/app/server/asgi.py` and its transaction tests), and
`docs/providers.md` records why this — ports, adapters, and scoped
resources — is Tenchi's whole integration story rather than a tier of
provider packages.

Run it with any ASGI server:

```sh
uvicorn app.server.asgi:app --reload
curl -X POST localhost:8000/todos -H 'content-type: application/json' \
  -d '{"title": "Buy milk"}'
```

## Hooks and authentication

Authentication belongs at the HTTP boundary; business authorization belongs
in use cases. The boundary seam is `create_app(hooks=...)`: each hook
receives a `RequestInfo` (method, path, lowercased headers, and the matched
contract) plus the request context, runs before input validation, and
either raises an `AppError` to reject or returns an enriched context to
attach identity:

```python
# app/server/hooks.py
from dataclasses import replace
from tenchi.errors import AppError
from tenchi.server import RequestInfo

def require_api_key(info: RequestInfo, context: AppContext) -> AppContext | None:
    if "public" in info.contract.tags:
        return None
    key = info.headers.get("x-api-key")
    if key is None:
        raise AppError(unauthorized)
    return replace(context, user=lookup_user(key))
```

Hook-raised errors follow the same honesty rule as use-case errors: they
must be declared to be exposed. Declare them once for a whole group — this
also documents the 401 on every route in the OpenAPI document:

```python
# app/server/routes.py
api_routes = route_group(todo_routes, errors=(unauthorized,))
```

The todos example wires an optional API-key hook this way; see
`examples/todos/app/server/hooks.py`.

## Typed client

The same contracts drive a typed `httpx`-based client — no code generation,
no drift. `call()` returns the contract's response type, so `todo` below is
statically a `Todo` and `todos` a `list[Todo]`:

```python
from tenchi.client import Client

async with Client(base_url="http://localhost:8000") as client:
    todo = await client.call(create_todo_contract, request=CreateTodo(title="Buy milk"))
    todos = await client.call(list_todos_contract, query=ListTodosQuery(completed=False))
```

Declared errors come back as the same `AppError` the server raised, carrying
the same `ErrorDef`; anything undeclared raises `UnexpectedResponseError`:

```python
try:
    await client.call(get_todo_contract, params=GetTodoParams(todo_id="missing"))
except AppError as err:
    assert err.definition == todo_not_found
```

The client owns its transport: pass `headers=` for defaults sent on every
request (such as an `authorization` header), and `transport=` to call an
app in-process in tests:

```python
async with Client(
    transport=httpx.ASGITransport(app=app),
    headers={"authorization": "Bearer ..."},
) as client:
    ...
```

A fully configured `httpx.AsyncClient` can still be supplied via
`Client(http=...)`; the caller keeps ownership of it.

## Pagination

`tenchi.pagination` standardizes offset pagination: subclass `PageQuery`
to add filters, use `Page[Item]` as the contract response, and build
results with `page()`:

```python
from tenchi.pagination import Page, PageQuery, page

class ListTasksQuery(PageQuery):          # limit/offset with sane bounds
    status: TaskStatus | None = None

async def list_tasks(query: ListTasksQuery, context: AppContext) -> Page[Task]:
    items, total = await context.tasks.search(..., limit=query.limit, offset=query.offset)
    return page(items, total=total, query=query)
```

## Health

`health_route()` composes a health endpoint through Tenchi's own route
machinery. Checks receive the request context (so they can reach ports),
may be sync or async, and fail by raising — failures surface as a 503
`UNHEALTHY` envelope listing exception class names only, with full
tracebacks in the log:

```python
from tenchi.health import health_route

async def database_ready(context: AppContext) -> None:
    await context.todos.list()

routes = route_group(api_routes, health_route(checks={"database": database_ready}))
```

The route is tagged `health` so authentication hooks can exempt it via
`info.contract.tags`.

## Policies

Business authorization lives in `features/<feature>/policy.py` as plain
functions: an ability belongs to the feature that owns the *subject* it
inspects, policies take their subjects as arguments (no I/O), and use
cases fetch, then ask:

```python
# app/features/projects/policy.py
def ensure_can_write_project(user: User, project: Project | None, *, project_id: str) -> Project:
    if project is None:
        raise AppError(project_not_found, details={"project_id": project_id})
    if project.owner_id != user.id:
        raise AppError(forbidden, details={"project_id": project_id})
    return project

# app/features/tasks/use_cases/create_task.py — the ability lives with projects
project = await context.projects.get(request.project_id)
ensure_can_write_project(user, project, project_id=request.project_id)
```

`tenchi doctor` enforces the discipline three ways: policies may import
schemas, domain types, and shared errors — never infrastructure, the app
context, or the HTTP runtime; and once any use case in an app references
authorization (`require_user`, `context.user`, or a policy import), every
use case must do the same or carry an explicit `# doctor: public` pragma,
so a forgotten check is a finding rather than an open endpoint.

For confused-deputy protection, owner-scoped repository methods should
accept a scope object derivable only from the authenticated user instead
of a raw id string — so an id lifted from request input cannot be passed
by accident:

```python
@dataclass(frozen=True, slots=True)
class OwnerScope:
    owner_id: str

def require_owner_scope(user: User | None) -> OwnerScope: ...

# ports.py
async def list_owned_by(self, owner: OwnerScope) -> list[Project]: ...

# use case
owner = require_owner_scope(context.user)
return await context.projects.list_owned_by(owner)
```

The taskboard example demonstrates the full story: `OwnerScope` on every
owner-scoped port method, and a membership slice
(`POST /projects/{id}/members`, owner-only) where policies grant members
view access via fetch-then-ask — the use case fetches the subject through
a port, then asks the pure policy.

## Errors

Application errors carry a stable code, an HTTP status, and optional
structured details. Contracts declare the errors they are expected to
return; declared errors map to their status, and everything else — including
undeclared `AppError`s — becomes a framework-owned 500 so contracts stay
honest:

```python
# app/shared/errors.py
from tenchi.errors import ErrorDef

todo_not_found = ErrorDef(code="TODO_NOT_FOUND", status=404, message="Todo not found")
```

```python
# in a use case
raise AppError(todo_not_found, details={"todo_id": params.todo_id})
```

Errors can carry response headers — declare the names on the definition
(they appear in the OpenAPI document) and set values per instance:

```python
throttled = ErrorDef(code="THROTTLED", status=429, message="Slow down",
                     headers=("Retry-After",))
raise AppError(throttled, headers={"Retry-After": "30"})
```

```python
# in a contract
get_todo_contract = contract(
    method="GET",
    path="/todos/{todo_id}",
    params=GetTodoParams,
    response=Todo,
    errors=(todo_not_found,),
)
```

Error responses use a flat envelope, `{"code", "message", "details"?}`, and
every error response carries an `x-tenchi-error-source` header set to `app`
or `framework` so the two are always distinguishable.

## Testing

Use cases test without HTTP — construct a context with a fake or memory
adapter and call the function:

```python
async def test_create_todo() -> None:
    context = AppContext(todos=MemoryTodoRepository())
    todo = await create_todo(CreateTodo(title="Buy milk"), context)
    assert todo.title == "Buy milk"
```

Integration tests use `tenchi.testing`, which runs the app's lifespan
around an in-process client (`httpx.ASGITransport` alone never triggers
lifespan events):

```python
from tenchi.testing import open_client, open_http

async with open_client(app, headers={"authorization": "Bearer ..."}) as client:
    todo = await client.call(create_todo_contract, request=CreateTodo(title="x"))

async with open_http(app) as http:          # raw httpx for envelope assertions
    assert (await http.get("/nope")).status_code == 404
```

## OpenAPI

Contracts carry everything an OpenAPI document needs, so generation is a
pure function — no decorators, no runtime introspection of handlers:

```python
from tenchi.openapi import openapi_schema

document = openapi_schema(api_routes, title="Todos", version="0.1.0")
```

Request bodies use validation-mode JSON Schema, responses use
serialization mode, path/query parameters come from the `params`/`query`
models, declared errors appear as error responses under their status with
the standard envelope schema, and routes with validated input document the
framework's 422 automatically.

To serve the document, compose `openapi_route` alongside your routes in
`server/routes.py` — it is generated once at startup and served by the same
route machinery it describes (and it does not document itself):

```python
from tenchi.openapi import openapi_route

api_routes = route_group(todo_routes)
routes = route_group(
    api_routes,
    openapi_route(api_routes, title="Todos", version="0.1.0"),
)
```

## CLI

```sh
tenchi new my_app                      # scaffold a new application
tenchi make feature notes              # generate a feature skeleton
tenchi make use-case notes create_note # generate a use-case stub and test
tenchi routes                          # print the bound route table
tenchi openapi [-o openapi.json]       # print or write the OpenAPI document
tenchi doctor                          # check dependency direction and structure
tenchi dev                             # serve app.server.asgi:app with reload
```

Generators create files and print wiring instructions — they never edit
existing modules, because dependency wiring stays explicit and app-owned.
Everything they generate passes Ruff, Pyright strict, pytest, and
`tenchi doctor` as-is.

`tenchi doctor` statically enforces the dependency direction: use cases
that import concrete infrastructure, schemas that import the HTTP runtime,
shared code that depends on features, and similar violations are reported
with file, line, and the rule broken:

```txt
app/features/todos/use_cases/create_todo.py:1  imports app.infra.port_wiring: use cases must not import concrete infrastructure
```

`tenchi new` generates the todos starter — feature, ports, memory adapter,
wiring, and passing tests — so a new project starts from a working vertical
slice:

```sh
uv run tenchi new my_app
cd my_app && uv sync && uv run pytest
```

`tenchi routes` prints every bound route with its status, use case, and
declared error codes:

```txt
POST  /todos            201  app.features.todos.use_cases.create_todo.create_todo
GET   /todos            200  app.features.todos.use_cases.list_todos.list_todos
GET   /todos/{todo_id}  200  app.features.todos.use_cases.get_todo.get_todo  [TODO_NOT_FOUND]
GET   /openapi.json     200  tenchi.openapi.openapi_route.<locals>.get_openapi
```

## Example

A complete todos application using the prescribed structure lives in
[`examples/todos/`](examples/todos/). It ships two adapters for the same
port: the SQLite repository (aiosqlite) wired into the running app through
the lifespan, and the memory repository used by unit tests — swapping them
touches only `infra/` and `server/`.

The stress-test application lives in
[`examples/taskboard/`](examples/taskboard/): two related features
(projects and tasks), bearer-token authentication with identity on the
context, ownership rules in use cases, pagination, partial updates, and
SQLite adapters sharing one lifespan-managed connection. It is a
standalone uv project consuming tenchi as a dependency — if a framework
capability regresses, something there should break.

## Status

Tenchi is an early vertical slice: contracts (body, path, and query
validation), route binding, ASGI dispatch, lifespan-managed resources with
request-scoped context, ports, expected-error mapping, a contract-driven
typed client, OpenAPI 3.1 generation, and the full CLI (`new`,
`make feature`, `make use-case`, `routes`, `openapi`, `doctor`, `dev`).
Provider-backed infrastructure is planned but intentionally not started.
