# Tenchi

[![CI](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml/badge.svg)](https://github.com/taylorbryant/tenchi/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/tenchi.svg)](https://pypi.org/project/tenchi/)
[![Python](https://img.shields.io/pypi/pyversions/tenchi.svg)](https://pypi.org/project/tenchi/)

Tenchi is a contract-first Python framework for production backends that humans
and coding agents can build and verify.

HTTP contracts and AI-facing application tools define validated boundaries.
Plain async functions implement use cases. `typing.Protocol` ports and frozen
dataclasses keep dependencies explicit. Tenchi turns those declarations into a
server, typed client, OpenAPI, machine-readable application maps, compatibility
reports, and verification receipts.

The goal is a pit of success: generate the mechanical work, keep business
decisions visible, and verify structure, types, tests, architecture, and public
contracts before a change is considered complete.

> [!WARNING]
> Tenchi is pre-1.0. Minor releases may change public APIs while the framework
> settles. Read [stability and releases](https://tenchi.io/stability) before
> adopting it for a long-lived service.

[Read the documentation](https://tenchi.io/) ·
[Follow the quickstart](https://tenchi.io/getting-started) ·
[See how Tenchi compares](https://tenchi.io/comparisons)

## Why Tenchi

- **One executable contract.** The server validates requests and responses, the
  Python client validates both sides of a call, and OpenAPI 3.1 comes from the
  same declaration.
- **Ordinary application code.** Use cases are plain async functions, ports are
  protocols, and infrastructure is wired explicitly. Application behavior does
  not belong to HTTP or a dependency-injection container.
- **Changes are reviewable.** Canonical snapshots and conservative
  compatibility analysis identify additive, breaking, metadata-only, and
  unknown changes across HTTP, job messages, application tools, and AI
  evaluation policy.
- **Production concerns have a place.** Tenchi provides focused primitives and
  patterns for authentication boundaries, authorization, transactions,
  idempotency, rate limits, retries, signed webhooks, jobs, operational tasks,
  health, preflight, and observability without choosing your infrastructure.
- **Agents and humans use the same evidence.** Deterministic maps, dry-run
  generation, versioned JSON results, a coding-agent MCP server, change plans,
  and `tenchi verify` make automated changes inspectable by people and CI.
- **AI features reuse the application.** Typed application tools expose normal
  use cases through in-process callers or authenticated MCP. Evaluation gates
  apply app-owned metrics, deadlines, and budgets without choosing a model
  provider or agent runtime.

## Quickstart

Tenchi requires Python 3.12 or newer. Create and run a complete application
with [uv](https://docs.astral.sh/uv/):

```shell
uvx tenchi new my_app
cd my_app
uv sync
uv run tenchi check
uv run tenchi dev
```

The generated application includes a working todos feature, SQLite persistence,
a memory test adapter, direct use-case and HTTP tests, Swagger UI, OpenAPI and
other boundary snapshots, a protected verification policy, CI, and an
`AGENTS.md` guide for coding agents.

With the server running, create a todo:

```shell
curl -i \
  -H 'content-type: application/json' \
  -d '{"title":"Buy milk"}' \
  http://127.0.0.1:8000/todos
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) for Swagger UI or
follow [build a feature end to end](https://tenchi.io/build-a-feature) to carry
one operation through its contract, use case, persistence adapters, tests,
OpenAPI, and final verification receipt.

To add Tenchi to an existing project instead:

```shell
uv add tenchi
```

Follow [add Tenchi to an existing project](https://tenchi.io/existing-project)
for the first contract, use case, context, route, ASGI application, test, and
OpenAPI baseline.

## The core model

Schemas define application and boundary data:

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

A contract owns the HTTP boundary:

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

A use case owns behavior and receives app-defined dependencies through its
context:

```python
# app/features/todos/use_cases/create_todo.py
from app.server.context import AppContext

from ..schemas import CreateTodo, Todo


async def create_todo(request: CreateTodo, context: AppContext) -> Todo:
    return await context.todos.create(title=request.title)
```

A route binds the two values:

```python
# app/features/todos/routes.py
from tenchi.routes import route, route_group

from .contracts import create_todo_contract
from .use_cases.create_todo import create_todo


routes = route_group(
    route(create_todo_contract, create_todo),
)
```

`route()` checks the use-case parameters and return annotation against the
contract during application composition. At runtime, Tenchi validates the
request before the use case runs and validates the response before its request
scope commits.

Applications keep the same dependency direction as they grow:

```text
app/
  features/<feature>/   # contracts, schemas, ports, policies, use cases, routes
  shared/               # shared errors and domain concepts
  infra/                # concrete adapters for app-owned ports
  server/               # context, lifecycle, hooks, composition, ASGI app
tests/                  # cross-feature and HTTP integration tests
```

Read the [mental model](https://tenchi.io/concepts) and
[application architecture](https://tenchi.io/architecture) for the complete
placement and dependency rules.

## One workflow for humans, agents, and CI

Tenchi makes the intended development loop explicit:

```shell
# Inspect the current application and its relationships.
uv run tenchi map --feature todos

# After declaring complete_todo_contract, preview its use-case boundary.
uv run tenchi make use-case todos complete_todo \
  --from-contract app.features.todos.contracts:complete_todo_contract \
  --dry-run --json

# Run formatting, linting, types, tests, architecture, and snapshot checks.
uv run tenchi check

# In a repository with origin/main, verify against that historical commit.
uv run tenchi verify --base-ref origin/main --json
```

`tenchi check` answers whether the current application is internally coherent.
`tenchi verify` additionally requires a complete application map, compares
public boundaries and verification policy with the selected Git commit, and
binds its receipt to the exact source tree it observed. Contract-driven change
plans can require the generated structure and its exact pytest target to appear
in that receipt.

Inspection, generator-preview, discovery, diagnostic, execution, check, and
verification results support versioned, payload-safe JSON. Compatibility
reports use `--diff-format json`; raw OpenAPI and manifest output retain their
published document schemas. The `tenchi mcp` command exposes the same
inspection, preview, compatibility, check, and verification operations to
MCP-aware coding agents over stdio. Generated applications configure that
server in `.mcp.json`.

Read [the coding-agent workflow](https://tenchi.io/agents),
[connect the coding-agent MCP server](https://tenchi.io/mcp), and
[verify a generated change](https://tenchi.io/change-plans) for the complete
loop.

## Production boundaries without prescribed infrastructure

Tenchi owns application-level contracts and lifecycle guarantees. Your app
chooses the infrastructure behind them.

| Concern | Tenchi provides | Your application chooses |
| --- | --- | --- |
| HTTP | Contracts, validation, declared errors, typed client, OpenAPI | Middleware, deployment platform, proxy |
| Identity | Authentication hooks, context enrichment, authorization patterns | Identity provider, credentials, policy rules |
| Data | App-owned ports, scoped contexts, transaction patterns, test helpers | Database, ORM or driver, migrations |
| Reliability | Idempotency, rate-limit, retry, webhook, and job primitives | Durable stores, queue, scheduler, dead-letter policy |
| Operations | Health routes, deployment preflight, named tasks, payload-safe outcomes | Environment checks, runbooks, exporters |
| AI | Typed tools, authenticated MCP, evaluation gates | Models, prompts, agent loop, datasets, judges |

This division keeps the framework small while giving teams an explicit answer
for common production concerns. The [production handbook](https://tenchi.io/production)
covers configuration, transactions, concurrency, retries, background work,
observability, and deployment as one system.

## Build AI features on normal use cases

Application tools add a stable machine-facing contract around ordinary
application behavior:

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

The tool runner validates input and output, opens the same application
lifecycle and scoped context used by other entrypoints, preserves only declared
application errors, and masks unexpected failures. The same group can serve an
in-process agent or an authenticated application MCP server with caller-specific
discovery and explicit approval for destructive calls.

When behavior depends on a model, typed evaluation cases apply metric
thresholds, per-case deadlines, and optional token and cost budgets. Evaluation
policy is snapshotted and compared separately from model execution, so an agent
cannot silently weaken a release gate and still pass historical verification.

Read [application tools](https://tenchi.io/tools),
[serve tools over MCP](https://tenchi.io/tool-mcp), and
[AI evaluations](https://tenchi.io/evaluations).

## CLI at a glance

| Command | Purpose |
| --- | --- |
| `tenchi new` | Generate a complete application |
| `tenchi make` | Preview or generate features and use-case boundaries |
| `tenchi routes` / `tenchi map` | Inspect runtime routes and application structure |
| `tenchi check` | Run deterministic local quality and snapshot checks |
| `tenchi verify` | Produce historical compatibility and completion evidence |
| `tenchi openapi` / `jobs` / `tools` / `eval snapshot` | Write and compare public boundary manifests |
| `tenchi task` | Discover and run validated operational tasks |
| `tenchi preflight` | Check the target deployment environment read-only |
| `tenchi eval` | Discover or run app-owned AI evaluation suites |
| `tenchi mcp` | Serve coding-agent operations over stdio MCP |
| `tenchi dev` | Run the ASGI application locally |

Run `tenchi <command> --help` for options or read the
[CLI reference](https://tenchi.io/cli).

## Is Tenchi a fit?

Tenchi is designed for long-lived typed JSON APIs where application structure,
client behavior, AI-facing tools, and compatibility need to remain aligned.
It is a strong fit when the same use cases must run through HTTP, jobs, tasks,
scripts, or tools and when both agents and people need machine-checkable
evidence for a change.

Choose another framework when you need WebSockets, HTML templates, an ORM,
admin UI, background runtime, or a large integration ecosystem as built-in
features. Tenchi exposes Starlette, httpx, and Pydantic where direct access is
more useful than another abstraction, but it deliberately does not provide a
dependency-injection container, model SDK, agent loop, queue, or scheduler.

Read the [framework comparison](https://tenchi.io/comparisons) for a candid
comparison with FastAPI, Starlette, Litestar, and Django Ninja.

## Examples

- [`examples/todos`](https://github.com/taylorbryant/tenchi/tree/main/examples/todos)
  is the generated teaching application. It demonstrates authentication,
  SQLite persistence, memory-backed use-case tests, typed-client tests, and
  boundary snapshots.
- [`examples/taskboard`](https://github.com/taylorbryant/tenchi/tree/main/examples/taskboard)
  combines Tenchi's production capabilities in a larger application and its
  own standalone environment.
- [`examples/fieldnotes`](https://github.com/taylorbryant/tenchi/tree/main/examples/fieldnotes)
  is a cited-AI reference backend with background indexing, authenticated
  application tools and MCP, bounded provider calls, preflight, and evaluation
  gates. Its default provider runs without model credentials.

## Documentation

- [Quickstart](https://tenchi.io/getting-started)
- [Build a feature end to end](https://tenchi.io/build-a-feature)
- [Contracts](https://tenchi.io/contracts)
- [Use cases and ports](https://tenchi.io/application)
- [Testing](https://tenchi.io/testing)
- [Production handbook](https://tenchi.io/production)
- [Coding agents](https://tenchi.io/agents)
- [Module reference](https://tenchi.io/reference)

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
