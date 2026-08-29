# Tenchi

[![CI](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml/badge.svg)](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tenchi.svg)](https://pypi.org/project/tenchi/)
[![Python](https://img.shields.io/pypi/pyversions/tenchi.svg)](https://pypi.org/project/tenchi/)

**Typed Python APIs with explicit boundaries and plain application code.**

Tenchi keeps Pydantic validation at the boundary, behavior in plain async
functions, and infrastructure behind `typing.Protocol` ports. A contract
describes an HTTP operation; `route()` binds it to a use case; your application
wires the dependencies.

There is no dependency-injection container, base controller, ORM, queue,
scheduler, or model runtime hidden inside the framework.

> **Pre-1.0 software.** Minor releases may change public APIs. Read
> [stability and releases](https://tenchi.io/stability) before adopting Tenchi
> for a long-lived service.

[Documentation](https://tenchi.io/) ·
[First application](https://tenchi.io/getting-started) ·
[How Tenchi works](https://tenchi.io/concepts)

## Build and run an application

Tenchi requires Python 3.12 or newer. Create a working application with
[uv](https://docs.astral.sh/uv/):

```shell
uvx tenchi new my_app
cd my_app
uv sync
uv run tenchi check
uv run tenchi dev
```

Call the generated todos API from another terminal:

```shell
curl -i \
  -H 'content-type: application/json' \
  -d '{"title":"Buy milk"}' \
  http://127.0.0.1:8000/todos
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to inspect and call the same API
in a browser. The [first-application guide](https://tenchi.io/getting-started)
follows this request through the generated code and makes one behavior change.

## The application model

Every HTTP operation follows the same path:

```text
validated input -> contract + route -> async use case -> app-owned port -> adapter
```

A Pydantic model defines the boundary data:

```python
# app/features/todos/schemas.py
from pydantic import BaseModel, Field


class CreateTodo(BaseModel):
    title: str = Field(min_length=1)


class Todo(BaseModel):
    id: str
    title: str
    completed: bool
```

A contract declares the HTTP operation:

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

The use case contains the behavior. Its context is an application-owned frozen
dataclass, so dependencies stay visible and type checked:

```python
# app/features/todos/use_cases/create_todo.py
from app.server.context import AppContext

from ..schemas import CreateTodo, Todo


async def create_todo(request: CreateTodo, context: AppContext) -> Todo:
    return await context.todos.create(title=request.title)
```

The route makes the binding explicit:

```python
# app/features/todos/routes.py
from tenchi.routes import route, route_group

from .contracts import create_todo_contract
from .use_cases.create_todo import create_todo


routes = route_group(
    route(create_todo_contract, create_todo),
)
```

`route()` checks the function signature during application composition. At
runtime, Tenchi validates input before the use case and validates its result
before the request scope commits. The same contract can also drive OpenAPI and
the typed Python client.

Read [How Tenchi works](https://tenchi.io/concepts) for the complete mental
model or [Build a feature](https://tenchi.io/build-a-feature) to carry an
operation through persistence and tests.

## Add capabilities when you need them

The core model stays the same as the application grows:

- Add [errors](https://tenchi.io/errors),
  [authentication](https://tenchi.io/authentication), or the
  [typed client](https://tenchi.io/client) at the API boundary.
- Run the same use cases from [workers and scripts](https://tenchi.io/execution),
  [background jobs](https://tenchi.io/jobs), or
  [operational tasks](https://tenchi.io/tasks).
- Add [idempotency](https://tenchi.io/idempotency),
  [observability](https://tenchi.io/observability), and
  [deployment checks](https://tenchi.io/production) when preparing to ship.
- Expose selected use cases as [application tools](https://tenchi.io/tools) or
  use [coding-agent workflows](https://tenchi.io/agents) when those capabilities
  fit the project.

These features are independent. A Tenchi application does not need jobs, AI
tools, evaluations, or historical compatibility checks to define and serve an
HTTP API.

## When Tenchi fits

Choose Tenchi when you want a long-lived typed JSON API with explicit
dependencies, directly testable behavior, and boundary definitions that stay
aligned with OpenAPI and client code.

Choose another framework when you need WebSockets, HTML templates, an ORM,
admin UI, background runtime, or a large integration ecosystem as built-in
features. The [framework comparison](https://tenchi.io/comparisons) describes
the tradeoffs with FastAPI, Starlette, Litestar, and Django Ninja.

## Development

```shell
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

The documentation is a separate Next.js application in `docs/`. Run
`bun install` and `bun run check` from that directory to lint, type-check, test,
and build the static site.

Tenchi is available under the
[MIT License](https://github.com/taylorbryant/tenchi/blob/main/LICENSE).
