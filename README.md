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

Contracts can also declare path parameters (`params=`) and query parameters
(`query=`), each validated into its own model and passed to the use case as
a keyword argument of the same name:

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

Run it with any ASGI server:

```sh
uvicorn app.server.asgi:app --reload
curl -X POST localhost:8000/todos -H 'content-type: application/json' \
  -d '{"title": "Buy milk"}'
```

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

For tests, pass your own `httpx.AsyncClient` with an `ASGITransport` via
`Client(http=...)` to call the app in-process.

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

Integration tests exercise the full boundary with `httpx.ASGITransport`; see
`examples/todos/tests/test_todos_http.py`. When the app uses a lifespan,
wrap it in `asgi-lifespan`'s `LifespanManager` so startup and shutdown run
(`ASGITransport` alone does not trigger lifespan events); see
`examples/todos/tests/test_todos_lifespan.py`.

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
tenchi dev                             # serve app.server.asgi:app with reload
```

Generators create files and print wiring instructions — they never edit
existing modules, because dependency wiring stays explicit and app-owned.
Everything they generate passes Ruff, Pyright strict, and pytest as-is.

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

## Status

Tenchi is an early vertical slice: contracts (body, path, and query
validation), route binding, ASGI dispatch, lifespan-managed resources with
request-scoped context, ports, expected-error mapping, a contract-driven
typed client, OpenAPI 3.1 generation, and a CLI (`new`, `make feature`,
`make use-case`, `routes`, `openapi`, `dev`). `tenchi doctor` and
provider-backed infrastructure are planned but intentionally not started.
