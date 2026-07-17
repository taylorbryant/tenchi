# Tenchi repository guide

Tenchi is a contract-first, Python-native framework for building typed
JSON APIs around use cases, ports, and explicit dependency wiring: one
architecture — contracts at the boundary, plain use cases at the center —
expressed with plain functions, frozen dataclasses, `typing.Protocol`,
Pydantic v2, and Starlette.

This file is the root instruction set for the repo. Follow it when changing
framework code, the CLI, docs, or the example apps.

## Operating principles

- Python-native ergonomics beat parity with any other framework's API.
  When adopting an idea from elsewhere, port the concept, not the
  spelling. When Python's constraints justify a departure, make it
  deliberately and record it (README, docstrings, this file).
- Prefer plain functions, frozen dataclasses, protocols, type annotations,
  and ordinary imports over builders, decorators, metaclasses, or
  inheritance. No fluent APIs.
- Framework machinery stays small and understandable. Every module in
  `src/tenchi/` should be readable in one sitting.
- Static typing must be genuinely useful (Pyright strict everywhere);
  runtime validation is Pydantic's job, at the boundary only.
- Fail at composition time, not request time: `route()` validates use-case
  signatures at import, `create_app()` validates context-factory arity and
  duplicate routes, `contract()` validates its own arguments.
- Tenchi is pre-1.0 and favors the clean current API over backwards
  compatibility. Do not preserve experimental designs out of caution.

## Repo orientation

- `src/tenchi/` is the framework package. One module per responsibility:
  - `contracts.py` — contract declarations (pure data; validation happens
    in consumers via `TypeAdapter`).
  - `pagination.py` — `Page[Item]`, `PageQuery`, and `page()`.
  - `health.py` — `health_route()` and the `UNHEALTHY` error definition.
  - `testing.py` — `open_client`/`open_http`, in-process clients that run
    the app lifespan.
  - `routes.py` — route/route-group binding with eager signature checks.
  - `errors.py` — `ErrorDef`, `AppError`, framework error definitions, the
    standard envelope.
  - `server.py` — `create_app`, lifespan/state, hooks, request dispatch.
  - `execution.py` — `execute`/`open_context`: run use cases with the
    server's boundary guarantees from any entrypoint (workers, scripts).
  - `client.py` — the contract-driven typed httpx client.
  - `openapi.py` — OpenAPI 3.1 generation (`openapi_schema` is a pure
    function; `openapi_route` serves it through Tenchi's own machinery).
  - `compatibility.py` — conservative compatibility analysis between two
    Tenchi-generated OpenAPI documents.
  - `_schema_compatibility.py` — directional JSON Schema comparison used by
    the compatibility analyzer.
  - `snapshots.py` — canonical OpenAPI snapshot rendering and readable drift
    diagnostics used by the CLI.
  - `doctor.py` — dependency-direction and structure checks.
  - `cli.py` + `scaffold.py` — the `tenchi` CLI and its string templates.
- `tests/` — framework tests, roughly one file per module plus
  cross-cutting files (`test_hooks.py`, `test_lifespan.py`,
  `test_request_scope.py`, `test_request_ids.py`, `test_middleware.py`,
  `test_cli.py`) and the API snapshot pair (`test_api_snapshot.py`,
  `api_snapshot.txt`).
- `examples/todos/` — the teaching app. Keep it minimal and aligned with
  the scaffold; it demonstrates each capability once.
- `examples/taskboard/` — the stress-test app, a standalone uv project
  consuming tenchi as a path dependency. It exercises capabilities
  together under realistic pressure. If a framework capability regresses,
  something here should break.
- `CHANGELOG.md` — Keep a Changelog format; maintain an `[Unreleased]`
  section during a development cycle.
- `.github/workflows/ci.yml` — checks for the root project and separate
  steps for taskboard's own environment. `release.yml` — tag-triggered
  PyPI publishing via trusted publishing.

## Non-negotiable change rule

When changing a public API, convention, generated file, or documented
workflow, update every surface that teaches or depends on it in the same
change:

- framework source and its tests
- `README.md`
- `CHANGELOG.md` (`[Unreleased]` section) when the published package changes
- the `tenchi new` scaffold and `make` templates in `src/tenchi/scaffold.py`
- `examples/todos` when the capability should be demonstrated publicly
- `examples/taskboard` when the change affects real-app ergonomics
- `doctor.py` when architecture conventions change
- CI when the check matrix changes

Before finishing, check for drift between these surfaces explicitly.

## Canonical app structure

Applications, the scaffold, both examples, and all docs use this layout:

```txt
app/
  features/<feature>/
    contracts.py   # HTTP boundary: method, path, inputs, response, errors
    schemas.py     # Pydantic models shared by contracts, use cases, ports
    ports.py       # typing.Protocol interfaces the feature needs
    policy.py      # authorization rules; abilities live with their subject
    routes.py      # binds contracts to use cases via route()/route_group()
    use_cases/     # one plain async function per module
    tests/         # use-case tests, no HTTP required
  shared/          # app-wide errors and shared-kernel concepts (users, ...)
  infra/           # concrete adapters + port_wiring
  server/
    context.py     # frozen AppContext dataclass of ports (+ user identity)
    hooks.py       # HTTP-boundary hooks (authentication)
    routes.py      # composes feature groups; group-level error declarations
    asgi.py        # concrete wiring, lifespan, hooks; exposes `app`
tests/             # integration tests over HTTP / the typed client
```

Dependency direction is enforced by `tenchi doctor` and must hold in every
example and template:

- Schemas, domain code, and ports never import infrastructure, server
  composition, or the HTTP runtime.
- Use cases may import schemas, ports, policies, `app.server.context`,
  and shared code — never concrete infrastructure, other server modules,
  routes, or the Starlette/Tenchi runtime.
- Policies take their subjects as arguments (no I/O, no context); an
  ability lives in the feature that owns the subject it inspects, and
  read-path ownership failures surface as not-found, not forbidden.
- Routes bind contracts to use cases; they never import infrastructure.
- Shared code never depends on features.
- Infrastructure implements ports; it never imports use cases, routes,
  contracts, or server composition.
- Server composition is the root and may import anything.
- Framework code (`src/tenchi/`) never depends on application code.

## Public API coherence

Naming:

- Modules are short plural nouns (`contracts`, `routes`, `errors`).
- Declarations are lowercase factory functions returning frozen dataclasses:
  `contract()`, `route()`, `route_group()`.
- Runtime constructors are `create_*` (`create_app`, `create_bearer_hook`).
- Async-context-manager factories are `open_*` (`open_request_ports`,
  `open_sqlite_todo_repository`).
- Application errors are `ErrorDef` module constants in `app/shared/errors.py`
  with stable `SCREAMING_SNAKE` codes.
- Adapters are named `<implementation>_<port>` modules exposing
  `<Implementation><Port>` classes (`memory_todo_repository.py`,
  `SqliteTaskRepository`).

API shape:

- Options are keyword-only. Positional arguments only where there is
  exactly one natural reading (`route(contract, use_case)`).
- Boundary validation accepts any type Pydantic can validate (via
  `TypeAdapter`), not just `BaseModel` subclasses.
- Do not hide httpx, Starlette, or Pydantic where exposing them is more
  useful (e.g. `Client(transport=...)`, lifespans as async context
  managers).
- Error messages name the contract, function, or file involved.

## Errors and auth

- The error envelope is flat: `{code, message, details?}`. Every error
  response carries `x-tenchi-error-source: app | framework`.
- Honesty rule: an `AppError` maps to its status only if the contract
  declares it; undeclared errors become framework-owned 500s. This applies
  to hooks too. `route_group(errors=...)` declares across a group;
  `Client(errors=...)` is the client-side counterpart. Keep server and
  client error semantics symmetric.
- Doctor's authorization consistency check: once any use case in an app
  references authorization, every use case must (or carry the explicit
  `# doctor: public` pragma). Keep example apps fully guarded.
- Owner-scoped repository methods take a scope object derivable only from
  the authenticated user (see taskboard's `OwnerScope`), never a raw id
  string.
- Membership-style rules stay in policies via fetch-then-ask: the use
  case fetches the subject through a port, the pure policy decides.
- Authentication lives in hooks at the HTTP boundary; hooks attach
  identity by returning an enriched (replaced) context. Business
  authorization lives in use cases, which still assert identity via an
  app-owned `require_user`-style helper. Ownership failures on reads
  surface as not-found, not forbidden, so ids cannot be probed.

## CLI expectations

The CLI is product surface. Generated code must pass Ruff, Ruff format,
Pyright strict, pytest, and `tenchi doctor` untouched — CI-grade, as
generated. Generators create files and print wiring instructions; they
never edit existing modules. `routes`, `openapi`, `doctor`, and `dev` rely
on the structural conventions (`app.server.routes:routes`,
`app.server.asgi:app`); keep flags available to override, and keep
`tenchi new` output aligned with `examples/todos` minus capabilities the
starter intentionally omits.
`openapi --write`, `openapi --check`, and `openapi --diff` use the same
canonical format; checked-in example and generated-app snapshots must be
reproducible with their documented metadata and security options. Run
`openapi --diff` before accepting a changed snapshot: breaking and unknown
changes fail, while additive and metadata-only changes pass. CI compatibility
checks must obtain their baseline from the pull-request base or preceding push;
comparing against the snapshot committed in the same change is only an equality
check and belongs to `openapi --check`.

## Testing conventions

- pytest with `asyncio_mode = "auto"`; tests are plain async functions.
- Use cases are tested without HTTP against memory adapters.
- Integration tests go through `tenchi.testing` (`open_client` for the
  typed client, `open_http` for raw envelope assertions); both run the
  app lifespan.
- Generated OpenAPI documents are validated with `openapi-spec-validator`.
- Findings from the taskboard app become framework issues (fix in
  `src/tenchi/`), never local workarounds in the app.

## Versioning and releases

- Semantic versioning with pre-1.0 semantics; minor versions may change
  the API.
- Bump `pyproject.toml` and `src/tenchi/__init__.py::__version__` together
  at the *start* of a development cycle, so `main` never claims a version
  PyPI already owns.
- Maintain `CHANGELOG.md` `[Unreleased]` as changes land; retitle it to
  the version on release.
- Release motion: merge to `main`, create a GitHub Release with tag
  `v<version>` (notes lifted from the CHANGELOG). The tag-triggered
  workflow verifies the tag matches the project version, re-runs all
  checks, and publishes via PyPI trusted publishing (environment `pypi`).

## Verification checklist

Run before finishing any change:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
uv run --directory examples/taskboard pytest
uv run --directory examples/taskboard pyright
uv run --directory examples/taskboard tenchi doctor
```

For changes to the CLI or scaffold, also generate a fresh app plus a
feature and use case in a temporary directory and confirm all of the above
pass inside it.

Changes to the public API surface fail `tests/test_api_snapshot.py` by
design. If the change is intentional, regenerate the snapshot with
`TENCHI_UPDATE_API_SNAPSHOT=1 uv run pytest tests/test_api_snapshot.py`,
review the diff of `tests/api_snapshot.txt` as part of the change, and
describe the API change in the changelog. Never regenerate to silence a
failure you did not intend to cause.
