# Tenchi

[![CI](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml/badge.svg)](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tenchi.svg)](https://pypi.org/project/tenchi/)
[![Python](https://img.shields.io/pypi/pyversions/tenchi.svg)](https://pypi.org/project/tenchi/)

**Production Python backends with explicit architecture and machine-verifiable
changes.**

Tenchi is a contract-first framework for backends that humans and coding agents
can build together. It keeps the HTTP server, typed client, OpenAPI, application
tools, use cases, and compatibility checks aligned around the same declarations.

Tenchi optimizes for what happens after the first endpoint: the application
grows, infrastructure changes, an agent edits several layers at once, and you
still need to know whether the result is wired correctly and safe to ship.

The model stays deliberately Python-native: Pydantic at the boundary, plain
async functions for behavior, `typing.Protocol` for ports, frozen dataclasses
for context, Starlette for ASGI, and httpx for client I/O. There is no
dependency-injection container, base controller, ORM, queue, scheduler, or
model runtime hidden inside the framework.

> **Pre-1.0 software.** Tenchi is ready for evaluation and real application
> feedback, but minor releases may change public APIs. Read
> [stability and releases](https://tenchi.io/stability) before adopting it for
> a long-lived service.

[Documentation](https://tenchi.io/) ·
[Quickstart](https://tenchi.io/getting-started) ·
[Comparisons](https://tenchi.io/comparisons)

## Start in five commands

Tenchi requires Python 3.12 or newer. Create a complete application with
[uv](https://docs.astral.sh/uv/):

```shell
uvx tenchi new my_app
cd my_app
uv sync
uv run tenchi check
uv run tenchi dev
```

The generated application is intentionally more than a hello-world route. It
includes a working todos feature, SQLite persistence, a memory test adapter,
direct use-case and HTTP tests, OpenAPI and other boundary snapshots, CI, a
repository-owned verification policy, and an `AGENTS.md` guide that gives coding
agents the same workflow.

While the server runs, create a todo from another terminal:

```shell
curl -i \
  -H 'content-type: application/json' \
  -d '{"title":"Buy milk"}' \
  http://127.0.0.1:8000/todos
```

Open [Swagger UI](http://127.0.0.1:8000/docs), follow the
[complete quickstart](https://tenchi.io/getting-started), or
[build a persisted feature end to end](https://tenchi.io/build-a-feature).

Adding Tenchi to an existing project starts with `uv add tenchi`; the
[existing-project guide](https://tenchi.io/existing-project) builds the first
contract, use case, context, route, ASGI application, test, and OpenAPI baseline.

## One architecture, several entrypoints

Tenchi keeps transport and infrastructure around the application instead of
inside it:

```text
HTTP contract ─────┐
Application tool ──┼──> plain async use case ──> app-owned ports ──> adapters
Job / task / script┘
```

A Pydantic model defines boundary data:

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

A contract owns the HTTP method, path, inputs, response, errors, and metadata:

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

The use case owns behavior and depends only on an app-defined context:

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

`route()` checks the use-case signature against the contract during application
composition. At runtime, Tenchi validates the request before behavior runs and
the response before its scoped context commits. The same contract drives the
async Python client and OpenAPI 3.1, so those surfaces cannot quietly drift.

The [mental model](https://tenchi.io/concepts) and
[application architecture](https://tenchi.io/architecture) explain where
contracts, policies, ports, adapters, and composition belong as the app grows.

## Verification is part of the framework

Tenchi gives people, agents, and CI one completion loop:

```shell
# Inspect declarations, registrations, dependencies, and diagnostics.
uv run tenchi map --feature todos

# After declaring complete_todo_contract, preview its use-case boundary.
uv run tenchi make use-case todos complete_todo \
  --from-contract app.features.todos.contracts:complete_todo_contract \
  --dry-run --json

# Check the current application.
uv run tenchi check

# Compare the finished tree with an immutable historical commit.
uv run tenchi verify --base-ref origin/main --json
```

`tenchi check` runs formatting, linting, Pyright, pytest, architecture checks,
and exact boundary-snapshot checks. `tenchi verify` also requires a complete
application map, compares public boundaries and verification policy with the
selected Git commit, and binds its receipt to the exact source tree it observed.

OpenAPI, durable job messages, application tools, and AI evaluation policy each
have canonical manifests and directional compatibility reports. Breaking and
unknown changes fail; additive and metadata changes remain visible for review.
Contract-driven change plans can additionally require exact generated files,
bindings, and pytest execution evidence in the final receipt.

Tenchi cannot prove that an application's business rules are correct. It can
prove which checks ran against which source, reject invalid wiring before
traffic arrives, and prevent a changed snapshot or weakened gate from hiding an
incompatible change.

Inspection, generator-preview, discovery, diagnostic, execution, check, and
verification results support versioned, payload-safe JSON. `tenchi mcp` serves
the same inspection, preview, compatibility, check, and verification operations
to MCP-aware coding agents over stdio. Generated applications register that
server in `.mcp.json`.

Read [the coding-agent workflow](https://tenchi.io/agents),
[connect the coding-agent MCP server](https://tenchi.io/mcp), and
[verify a generated change](https://tenchi.io/change-plans).

## AI features are application features

Tenchi does not own models, prompts, agent loops, memory, or retrieval. Those
choices sit behind app-owned ports. Tenchi makes the resulting capabilities
safe to expose and possible to verify.

An application tool binds a stable machine-facing contract to an ordinary use
case:

```python
from app.shared.errors import unauthorized
from tenchi.tools import tool, tool_group, tool_handler

from .schemas import Project
from .use_cases.list_projects import list_projects


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

The runner validates input and output, supplies identity through application
context rather than model-controlled arguments, exposes only declared errors,
and masks unexpected failures. The same tool group can serve an in-process
agent or an authenticated application MCP server with caller-specific discovery
and explicit approval for destructive calls.

Typed evaluation cases add application-owned metrics, deadlines, and optional
token and cost budgets. Evaluation policy is snapshotted separately from model
execution, so historical verification can detect a removed case, lowered
threshold, or expanded budget without running a provider.

Read [application tools](https://tenchi.io/tools),
[serve tools over MCP](https://tenchi.io/tool-mcp), and
[AI evaluations](https://tenchi.io/evaluations). The
[Fieldnotes example](https://github.com/taylorbryant/tenchi/tree/main/examples/fieldnotes)
shows the complete pattern in a cited research backend that runs without model
credentials by default.

## Production concerns have an explicit home

Tenchi supplies application-level boundaries and leaves infrastructure choices
to the application:

- **HTTP:** typed requests, successful responses and headers, declared errors,
  media types, deadlines, pagination, a runtime client, and OpenAPI.
- **Identity:** boundary authentication hooks, context enrichment, pure policy
  functions, and authorization in use cases.
- **Reliability:** transaction patterns, idempotency, rate limits, bounded
  outbound retries, signed webhooks, and queue-neutral job messages.
- **Operations:** health routes, read-only deployment preflight, validated
  operational tasks, payload-safe outcomes, and an OpenTelemetry bridge.
- **Testing:** direct use-case tests, lifespan-aware in-process HTTP and typed
  clients, and adapter conformance suites for stateful primitives.

Your application still chooses its database, ORM or driver, identity provider,
queue, scheduler, cache, exporters, model providers, and deployment platform.
The [production handbook](https://tenchi.io/production) connects those choices
to transactions, concurrency, retries, background work, observability, and
deployment.

## When Tenchi fits

Choose Tenchi for a long-lived typed JSON API when:

- server behavior, Python clients, OpenAPI, and AI-facing tools must remain
  aligned;
- the same behavior must run through HTTP, jobs, tasks, scripts, or tools;
- explicit dependencies and application structure are worth modest up-front
  ceremony; and
- humans and agents need the same machine-checkable completion evidence.

Choose another framework when you need WebSockets, HTML templates, an ORM,
admin UI, background runtime, or a large integration ecosystem as built-in
features. Read the [framework comparison](https://tenchi.io/comparisons) for a
candid comparison with FastAPI, Starlette, Litestar, and Django Ninja.

## Read next

- [Quickstart](https://tenchi.io/getting-started)
- [Build a feature end to end](https://tenchi.io/build-a-feature)
- [Contracts](https://tenchi.io/contracts)
- [Use cases and ports](https://tenchi.io/application)
- [Testing](https://tenchi.io/testing)
- [Production handbook](https://tenchi.io/production)
- [Coding agents](https://tenchi.io/agents)
- [Module reference](https://tenchi.io/reference)

The generated [`todos`](https://github.com/taylorbryant/tenchi/tree/main/examples/todos)
app teaches the core model. The standalone
[`taskboard`](https://github.com/taylorbryant/tenchi/tree/main/examples/taskboard)
app exercises capabilities together under realistic pressure.

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
