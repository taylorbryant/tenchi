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
  - `idempotency.py` — canonical input fingerprints, durable store transitions,
    typed replay, the memory test adapter, and standard conflict/in-progress
    errors.
  - `rate_limits.py` — atomic fixed-window limits and the memory test adapter.
  - `testing.py` — in-process clients that run the app lifespan and reusable
    idempotency/rate-limit adapter conformance checks.
  - `routes.py` — route/route-group binding with eager signature checks.
  - `errors.py` — `ErrorDef`, `AppError`, framework error definitions, the
    standard envelope.
  - `server.py` — `create_app`, lifespan/state, hooks, request dispatch.
  - `webhooks.py` — exact-body verifier bindings for signed inbound deliveries.
  - `execution.py` — `execute`/`open_context`: run use cases with the
    server's boundary guarantees from any entrypoint (workers, scripts).
  - `client.py` — the contract-driven typed httpx client and payload-safe
    outbound outcomes.
  - `opentelemetry.py` — the optional payload-safe OpenTelemetry observer
    bundle; applications own SDK providers, exporters, and shutdown.
  - `retries.py` — explicit bounded retry policies for transport failures,
    declared error codes, and selected raw HTTP statuses.
  - `jobs.py` — queue-neutral job declarations, messages, handler bindings,
    validated dispatch, and versioned durable-message manifests.
  - `tools.py` — transport-neutral application-tool declarations, explicit
    use-case bindings, lifecycle-aware execution, and deterministic manifests.
  - `evaluations.py` — provider-neutral typed evaluation cases, metrics,
    lifecycle-aware execution, timeouts, budgets, and redacted outcomes.
  - `mcp.py` — the optional authenticated application MCP adapter over a
    registered tool group; applications own identity, visibility, and approval.
  - `openapi.py` — OpenAPI 3.1 generation (`openapi_schema` is a pure
    function; `openapi_route` serves it and `swagger_ui_route` serves an
    optional interactive UI through Tenchi's own machinery).
  - `compatibility.py` — conservative compatibility analysis for
    Tenchi-generated OpenAPI documents, job-message manifests, application-tool
    manifests, and evaluation-policy manifests.
  - `_schema_compatibility.py` — directional JSON Schema comparison used by
    the compatibility analyzer.
  - `snapshots.py` — canonical OpenAPI, job-message, application-tool, and
    evaluation-policy snapshot rendering and readable drift diagnostics used
    by the CLI.
  - `doctor.py` — dependency-direction and structure checks.
  - `cli.py` + `scaffold.py` — the `tenchi` CLI and its string templates.
  - `_generation.py` — contract-driven source rendering and the explicit
    incomplete marker enforced by doctor.
  - `_change_plans.py` — versioned, content-addressed structural intent for
    contract-driven generation.
  - `_agent_protocol.py` — the authoritative result-name adapters used for
    CLI validation and canonical agent-facing JSON Schema generation.
  - `_verify_operations.py` — the unified check, architecture, and historical
    compatibility receipt shared by the CLI and coding-agent MCP server.
  - `_source_identity.py` + `_git.py` — exact Git-visible worktree identity and
    isolated Git process configuration for source-bound verification receipts.
  - `_mcp_server.py` — optional stdio MCP adapter over the CLI result
    operations for coding agents; it is loaded only when the `mcp` extra is
    installed and is separate from the public application adapter.
- `tests/` — framework tests, roughly one file per module plus
  cross-cutting files (`test_hooks.py`, `test_lifespan.py`,
  `test_request_scope.py`, `test_request_ids.py`, `test_middleware.py`,
  `test_cli.py`), the Python API snapshot pair (`test_api_snapshot.py`,
  `api_snapshot.txt`), and versioned agent-protocol snapshots guarded by
  `test_agent_protocol.py`.
- `examples/todos/` — the teaching app. Keep it minimal and aligned with
  the scaffold; it demonstrates each capability once.
- `examples/taskboard/` — the stress-test app, a standalone uv project
  consuming tenchi as a path dependency. It exercises capabilities
  together under realistic pressure. If a framework capability regresses,
  something here should break.
- `examples/fieldnotes/` — the cited-AI reference backend, also a standalone
  uv project. It exercises owner-scoped ingestion, durable background indexing,
  application tools and MCP, cited answers behind a provider port, operational
  reindexing, preflight, and evaluation gates without requiring model
  credentials in CI.
- `benchmarks/agent_changes/` — a vendor-neutral development benchmark that
  renders fresh generated applications, withholds repository-owned acceptance
  tests until evaluation, and records payload-safe agent and `verify` results.
  Normal CI validates the harness but never invokes a model.
- `CHANGELOG.md` — Keep a Changelog format; maintain an `[Unreleased]`
  section during a development cycle.
- `.github/workflows/ci.yml` — checks the current lock on Python 3.12–3.14,
  tests declared direct-dependency lower bounds on Python 3.12, and verifies
  the standalone example environments. `release.yml` — tag-triggered PyPI
  publishing via trusted publishing.

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
    jobs.py        # declares stable background messages via job()
    tasks.py       # binds operational names to use cases via task()/task_group()
    tools.py       # binds machine-facing contracts to use cases
    evaluations.py # declares typed AI evaluation cases, metrics, and evaluators
    use_cases/     # one plain async function per module
    tests/         # use-case tests, no HTTP required
  shared/          # app-wide errors and shared-kernel concepts (users, ...)
  infra/           # concrete adapters + port_wiring
  server/
    context.py     # frozen AppContext dataclass of ports (+ user identity)
    hooks.py       # HTTP-boundary hooks (authentication)
    webhooks.py    # signed-provider verification and service identity
    routes.py      # composes feature groups; group-level error declarations
    jobs.py        # binds job declarations to consumer use cases
    runtime.py     # resources shared by HTTP and operational entrypoints
    preflight.py   # read-only checks of the target deployment environment
    evaluations.py # composes the application evaluation runner
    tasks.py       # composes the operational task runner
    tools.py       # composes tools with authenticated context wiring
    mcp.py         # optionally exposes application tools over MCP
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
- Job declarations bind stable names to request/result types without importing
  consumers or infrastructure; producers serialize them with `job_message()`.
- Job handlers are bound at server composition. Queue adapters own persistence,
  claiming, acknowledgement, retry, scheduling, and dead-lettering.
- Preflight checks live at the composition root, open read-only clients, and
  never migrate, repair, enqueue, or return dependency values.
- Tasks bind stable operational names to use cases; they never import
  infrastructure.
- Tools bind stable machine-facing contracts to use cases; they never import
  infrastructure, HTTP contracts, or server composition. Safety annotations
  are descriptive hints, never authorization.
- Evaluations bind typed cases to provider-neutral evaluators; they may import
  schemas, ports, policies, and use cases, but never infrastructure, HTTP
  contracts, or server composition. Evaluators return scores and usage only.
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
- Authentication hooks exempt routes through `info.contract.public`, which
  defaults to `False`; documentation tags and paths never decide access.
  `health_route()` and `openapi_route()` are public by default and accept
  `public=False` when an application needs to protect them. OpenAPI derives
  global-security exemptions from the same contract metadata. Generated error
  responses enumerate their exact codes and application/framework source;
  every operation documents the framework-owned internal 500. Authentication
  statuses remain application-owned and appear only when declared.
- Signed inbound contracts declare `webhook=True`; `create_app()` must bind
  each one through `webhook(contract, verifier)`. Verifiers use the exact
  size-bounded bytes, raise declared `AppError` values on expected rejection,
  and may return an enriched context carrying service identity. Use cases
  still assert that identity, and provider event ids use transaction-scoped
  idempotency to collapse redelivery.
- Retry-safe operations use `run_idempotently()` with an `IdempotencyStore`
  supplied through the application context. The store that protects database
  writes participates in the same transaction; keys are scoped to an actor or
  tenant, and contracts declare both standard idempotency errors.
- HTTP contracts that implement that guarantee set `idempotency_key=True` and
  declare one required, non-empty string `Idempotency-Key` header. This is
  checked and published to OpenAPI; it never substitutes for
  `run_idempotently()` or correct transaction placement.
- Application-level quotas use `enforce_rate_limit()` with a `RateLimitStore`
  scoped from authenticated identity, never untrusted request input. Put the
  consume inside idempotent work when one logical operation should cost once.
  Edge request floods and unauthenticated abuse remain proxy or gateway
  concerns; `MemoryRateLimitStore` is for tests and local development only.
- Application tools receive identity through app-owned context wiring, never
  model-supplied input. Tool declarations list every caller-visible `ErrorDef`;
  undeclared application errors and unexpected exceptions stay behind the
  generic tool invocation failure.
- Application MCP authenticates both discovery and invocation, rechecks
  per-principal visibility before a call, and denies destructive tools without
  an application-owned approval decision. Discovery and approval never replace
  use-case authorization. Versioned `ok: false` envelopes remain structured
  tool results and set MCP's standard `isError` flag.

## CLI expectations

The CLI is product surface. New applications and structural generator output
must pass Ruff, Ruff format, Pyright strict, pytest, and `tenchi doctor`
untouched — CI-grade, as generated. Contract-driven use-case generation is the
deliberate exception: its boundary source passes formatting, lint, and typing,
while an exact `# tenchi: incomplete` marker and failing test must keep pytest,
doctor, and therefore `check` red until behavior and tests replace the
placeholders. Before planning files, `--from-contract` validates that the
declaration can bind as a route and produce OpenAPI. Generators create files and
print wiring instructions; they never edit existing modules. `routes`, `map`,
`tools`, `openapi`, `check`,
`verify`, `preflight`, `eval`, `mcp`, and `dev` rely on the structural conventions
(`app.server.routes:routes`, `app.server.routes:api_routes`,
`app.server.jobs:jobs`, `app.server.tools:tools`,
`app.server.preflight:checks`,
`app.server.evaluations:runner`,
`app.server.asgi:app`); keep flags available to
override, and keep `tenchi new` output aligned with `examples/todos` minus
capabilities the starter intentionally omits.
`map` combines source declarations with composed routes, operational tasks,
background jobs, application tools, and evaluations and must stay deterministic,
source-backed, and versioned in JSON. Feature projections retain directly
related cross-feature and shared nodes; kind projections never leave dangling
edges. `map` loads registered background jobs from `app.server.jobs:jobs` and
application tools from `app.server.tools:tools` by default.
`map` loads registered evaluations from `app.server.evaluations:runner` by
default and retains their source, kind, case count, metrics, timeout, and
budgets without case inputs.
`task list|run` loads `app.server.tasks:runner` by default. Tasks provide named,
validated operator entrypoints for backfills, repairs, replays, and maintenance;
they do not schedule, retry, queue, lock, or persist progress.
`preflight` is an environment-aware deployment gate separate from deterministic
`check`. Checks are zero-argument async observations with per-check timeouts,
static names and failure codes, redacted results, and no application context or
lifespan. They run concurrently and must use read-only credentials; mutations
belong in migrations or explicitly authorized operational tasks.
`eval list|run` loads `app.server.evaluations:runner` by default. Evaluation
cases and outputs are application-owned; results expose only stable names,
normalized scores, usage, status, durations, and failure codes. Runs use one
lifespan, one scoped context per case, bounded concurrency, per-case timeouts,
metric thresholds, isolated case inputs, and optional token/cost budgets.
Budget outcomes distinguish measured exceedance from unverified usage, and
token values remain within the interoperable JSON integer range. Evaluation
execution remains separate from deterministic `check` and `verify`.
`eval snapshot` uses the same target to print, write, check, and directionally
compare the versioned, payload-free `evaluations.json` policy. It never runs
evaluators. The manifest retains case names in execution order plus schemas,
metrics, thresholds, kind, timeout, and budgets but never case inputs. Removed
policy elements and weakened gates are incompatible; reordered cases, changed
case schemas, and unsupported fields fail closed for review. A missing
historical snapshot fails by default; first adoption requires the explicit
missing-baseline option and records an `evaluation manifest baseline` metadata
change. `check` performs
exact drift checking and `verify` compares the policy with the historical Git
baseline.
`mcp` is a thin, stdio-only adapter over the same renderer-independent
operations. Inspection and preview tools never write files, every path stays
inside the captured app root, stdout belongs exclusively to JSON-RPC, and the
`check` tool must cancel its active subprocess and its process group where the
platform supports one when the client cancels.
Task discovery is read-only. MCP task execution must remain explicitly opt-in,
propagate cancellation through lifespan and context cleanup, and never expose
task input in its result.
Evaluation discovery is read-only and never exposes case inputs. MCP evaluation
execution must remain explicitly opt-in, propagate cancellation through
lifespan and context cleanup, and never expose prompts, model outputs, context
values, or exception messages.
Application-tool manifests have their own `TOOL_MANIFEST_VERSION` and retained
versioned schema snapshots. Additive protocol changes may update the current
snapshot; breaking or unknown changes require a version bump and a new snapshot.
`tools --write`, `tools --check`, `tools --diff`, and Git-backed
`tools --diff-ref` use one canonical manifest format. Checked-in example and
generated-app snapshots must be reproducible; compare against a historical
baseline before replacing the snapshot.
Job-message manifests have their own `JOB_MANIFEST_VERSION` and retained
versioned schema snapshots. `jobs --write`, `jobs --check`, `jobs --diff`, and
Git-backed `jobs --diff-ref` use one canonical, payload-free manifest. Removing
a job or narrowing its accepted input is breaking because durable queues may
still contain older messages. `check` performs exact drift checking and
`verify` compares the manifest with the historical Git baseline. Every
`job_message()` serialization must satisfy the published input schema and pass
strict consumer revalidation. Compatibility proves that the new consumer can
read historical messages; deploy compatible consumers before new producers.
The public `tenchi.mcp` module is a separate adapter for an application's own
`ToolGroup`. Its MCP schemas and structured result envelope have their own
version and immutable snapshots; the coding-agent protocol version does not
govern them.
Structured CLI results and MCP tool inputs and outputs share one agent protocol
version. Additive changes may update its current canonical snapshot; breaking
or unknown changes require a version bump and a new snapshot, leaving earlier
versioned snapshots intact. Every CLI invocation that requests `--json` or
`--diff-format json` must emit exactly one validated JSON object on stdout for
expected success and failure paths. Failures that occur before the normal
operation result exists use the shared, redacted `operation_error` result and
retain a nonzero exit status; application exception text and payloads never
enter that result.
`openapi --write`, `openapi --check`, `openapi --diff`, and Git-backed
`openapi --diff-ref` use the same canonical format; checked-in example and
generated-app snapshots must be reproducible with their documented metadata and
security options. Standalone `openapi` defaults to
`app.server.routes:api_routes` and discovers literal `OPENAPI_TITLE`,
`OPENAPI_VERSION`, `OPENAPI_DESCRIPTION`, and `OPENAPI_SECURITY` declarations
from that module, matching `check` and `verify`. Run `openapi --diff` before
accepting a changed snapshot:
breaking and unknown changes fail, while additive and metadata-only changes
pass. CI compatibility checks must obtain their baseline from the pull-request
base or preceding push; prefer `--diff-ref` when that baseline is committed.
Comparing against the snapshot committed in the same change is only an equality
check and belongs to `openapi --check`.
Named request/response examples are validated and serialized against the exact
published schema. Example changes are metadata. Removing a documented
idempotency-key guarantee is breaking; adding one is additive only when its
required header already exists compatibly.
`verify --base-ref <ref>` resolves the ref once, runs `check`, requires an
application map with no diagnostics or unresolved relationships, and compares
OpenAPI, job-message, application-tool, and evaluation-policy snapshots with
that immutable commit. Generated applications declare required evidence in
`tenchi.toml`. Verification compares that policy with the same commit and
enforces the stronger current-or-historical requirement, so a gate cannot skip
itself while being weakened. Missing policy files retain Tenchi's strict
built-in requirements; malformed or removed repository policies fail closed.
The receipt distinguishes passed, failed, skipped, not-configured, and
not-verifiable evidence. It records the current HEAD, dirty state, and a
canonical digest of tracked and nonignored untracked paths below the application
root. Git repository-selection environment variables cannot redirect the
receipt to another checkout. Recheck that identity after every project-owned
execution or import and at the end; any observed persistent change fails the
receipt as a source error. It never writes
snapshots. A missing job snapshot
requires the explicit `--allow-missing-job-baseline` first-adoption override. A
missing evaluation snapshot requires the explicit
`--allow-missing-evaluation-baseline` first-adoption override. The CLI and
coding-agent MCP tool return the same versioned receipt and propagate
cancellation through source capture and the active check subprocess.
Contract-driven use-case generation can write a versioned change plan in the
same filesystem transaction as its generated files. The plan records a
content-derived identity, immutable Git baseline, exact contract and use-case
identity, generated paths, and fixed structural postconditions. `--dry-run`
returns the prospective plan without writing it. `verify --change-plan <path>`
requires the same baseline commit, both files without incomplete markers,
registered contract and use case, the current contract-derived signature, exact
route bindings, and a direct dependency from a top-level `test_*` feature-test
function identified by an exact pytest target. It requires at least one
collected invocation and successful setup, call, and teardown for every
invocation; skipped, xfailed, xpassed, deselected, failed, errored, uncollected,
or ambiguous targets fail. The target must resolve to exactly one top-level
definition with an unshadowed, unrebound direct use-case import, and the callable
collected by pytest must retain that definition's source identity; wrapping
decorators therefore fail closed. It reads the plan before and after
project-owned commands to detect mutation. Change-plan evidence proves
structural completion and test execution, not business correctness or assertion
quality. Its `project_pytest_process` provenance is cooperative evidence, not a
tamper-proof attestation against project-owned conftest files, plugins, or
imported test code running in that interpreter. Use an external evaluator with
withheld tests when the code producer is adversarial, and preserve the accepted
plan ID outside the edited worktree when exact intent needs independent review.

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
- Keep current releases in the root, taskboard, and Fieldnotes lockfiles, but
  preserve a dependency's declared lower bound until compatibility, security,
  or a required API proves that it must rise. The Python 3.12 `lowest-direct`
  CI job makes those published compatibility claims executable.
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
uv run --directory examples/fieldnotes pytest
uv run --directory examples/fieldnotes pyright
uv run --directory examples/fieldnotes tenchi doctor
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

Agent-facing schema changes fail `tests/test_agent_protocol.py`. For an
additive change, regenerate with
`TENCHI_UPDATE_AGENT_PROTOCOL_SNAPSHOT=1 uv run pytest
tests/test_agent_protocol.py` and review the canonical JSON Schema diff. The
updater refuses breaking or unknown changes at the current version; bump
`AGENT_PROTOCOL_VERSION`, update the embedded result version, and create a new
versioned snapshot instead. Never replace an earlier protocol snapshot.

Change-plan schema changes fail `tests/test_change_plans.py`. For an additive
change, regenerate with `TENCHI_UPDATE_CHANGE_PLAN_SNAPSHOT=1 uv run pytest
tests/test_change_plans.py` and review the canonical JSON Schema diff. Breaking
or unknown changes require bumping `CHANGE_PLAN_SCHEMA_VERSION`, creating a new
snapshot, and retaining earlier versions.

Application MCP schema changes fail `tests/test_tool_mcp.py`. Generate the
first snapshot for a new `TOOL_MCP_PROTOCOL_VERSION` with
`TENCHI_UPDATE_TOOL_MCP_SNAPSHOT=1 uv run pytest tests/test_tool_mcp.py`.
Existing snapshots are immutable; any changed application MCP wire shape
requires a version bump and a new snapshot.

Evaluation-manifest schema changes fail `tests/test_evaluations.py`. For an
additive change, regenerate with `TENCHI_UPDATE_EVALUATION_MANIFEST_SNAPSHOT=1
uv run pytest tests/test_evaluations.py` and review the canonical JSON Schema
diff. Breaking or unknown changes require bumping `EVALUATION_MANIFEST_VERSION`
and creating a new snapshot; retain earlier versions.

Job-manifest schema changes fail `tests/test_jobs.py`. For an additive change,
regenerate with `TENCHI_UPDATE_JOB_MANIFEST_SNAPSHOT=1 uv run pytest
tests/test_jobs.py` and review the canonical JSON Schema diff. Breaking or
unknown changes require bumping `JOB_MANIFEST_VERSION`, creating a new
snapshot, and retaining earlier versions.
