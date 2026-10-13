# Tenchi

[![CI](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml/badge.svg)](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tenchi.svg)](https://pypi.org/project/tenchi/)
[![Python](https://img.shields.io/pypi/pyversions/tenchi.svg)](https://pypi.org/project/tenchi/)

**A contract-first Python framework for humans and coding agents.**

Tenchi keeps Pydantic validation at the boundary, behavior in plain async
functions, and infrastructure behind `typing.Protocol` ports. A contract
describes an HTTP operation; `route()` binds it to a use case; your application
wires the dependencies.

The same development workflow lets you inspect a feature, preview its
structure, implement behavior, and review checks and compatibility evidence.

> **Pre-1.0 software.** Minor releases may change public APIs. Read
> [stability and releases](https://tenchi.io/stability) before adopting Tenchi
> for a long-lived service.

[Documentation](https://tenchi.io/) ·
[First application](https://tenchi.io/getting-started) ·
[How Tenchi works](https://tenchi.io/concepts) ·
[Development workflow](https://tenchi.io/agents)

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

The default starter includes the todos API, SQLite and memory adapters,
tests, and OpenAPI checks. It also includes `AGENTS.md`, project-local MCP
configuration, and `tenchi.toml` so people, coding agents, and CI share the
same application conventions and verification requirements.

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

## Make a reviewable change

1. Inspect the affected feature with `uv run tenchi map --feature todos` and
   read the source and tests it identifies.
2. Declare the boundary in a contract and preview generated files with
   `tenchi make ... --dry-run` before creating them.
3. Implement behavior in plain Python, wire dependencies explicitly, and
   write tests for the requirement.
4. Run `uv run tenchi check` for formatting, lint, types, tests, architecture,
   and snapshot drift. Review compatibility changes before accepting new
   snapshots.
5. Run `uv run tenchi verify --base-ref <historical-ref>` against the commit
   before the change, then review its receipt alongside the code and tests.

Use the pull-request base, previous push, or previous release for the
historical ref. `verify` needs committed baseline snapshots. A passing result
records the configured checks and compatibility evidence; reviewers still
judge whether the behavior and assertions meet the requirement.

[Coding agents](https://tenchi.io/agents) use these same commands with
`--json`, or access inspection, previews, and verification through the
[coding-agent MCP server](https://tenchi.io/mcp). You choose the agent and
model provider.

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

JSON responses are checked against their published schema and read back through
Pydantic before committing, including when a use case returns an existing model.
Response field aliases must be readable by that same model; use `Field(alias=...)`
for shared wire names. See [successful responses](https://tenchi.io/responses).

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
- Expose selected use cases as [application tools](https://tenchi.io/tools),
  serve them through [application MCP](https://tenchi.io/tool-mcp), or add
  [evaluations](https://tenchi.io/evaluations) when the application runs AI
  behavior.

Adopt these runtime capabilities individually. Select optional feature modules
with `tenchi make feature invoices --with jobs --with tools`. Preview tools
adoption with `tenchi make capability tools --dry-run --json`, then follow its
composition and verification-policy instructions. `tenchi new --full` includes
all optional composition modules when you need that starting point.

Jobs, tasks, tools, and `execute()` call functions with `request` and `context`.
For a use case that also requires HTTP-specific `params`, `query`, or `headers`,
add an [input adapter](https://tenchi.io/execution#adapt-http-inputs) to reuse its
behavior from those entrypoints.

## When Tenchi fits

Choose Tenchi when you want humans and coding agents to make backend changes
through explicit contracts, predictable source structure, directly testable
behavior, and reviewable verification evidence. The application model stays
small, while development tooling is part of the framework's main workflow.

Choose another framework when you need WebSockets, HTML templates, an ORM,
admin UI, background runtime, or a large integration ecosystem as built-in
features. The [framework comparison](https://tenchi.io/comparisons) describes
the tradeoffs with FastAPI, Starlette, Litestar, and Django Ninja.

## Development

Contributors should read [AGENTS.md](AGENTS.md) for repository rules and
[PRODUCT.md](PRODUCT.md) for the framework's goals and design tradeoffs.
Tenchi prioritizes a development workflow that humans and coding agents can
understand and verify, built on a small, contract-first Python application model.

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
