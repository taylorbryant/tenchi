# Changelog

All notable changes to Tenchi are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and Tenchi adheres to
[Semantic Versioning](https://semver.org/) with pre-1.0 semantics: minor
versions may change the public API.

## [0.6.0] - 2026-07-14

### Added

- `tenchi.execution`: `execute()` runs a use case from any entrypoint —
  worker, script, scheduler, test — with the server's boundary
  guarantees: input validated against the use case's own `request`
  annotation (Python data or raw JSON), undeclared inputs rejected, and
  the same context scoping as `create_app`. `open_context()` exposes
  that scoping directly, and the server now uses it too, so
  commit-on-success / rollback-on-error semantics are defined once.
  `docs/execution.md` records what was deliberately left out.

- Request body size limits: `create_app(max_request_bytes=...)` caps
  bodies app-wide (default 1 MiB) and `contract(max_request_bytes=...)`
  overrides per route with a finite ceiling. Oversized bodies — by
  declared `Content-Length` or by actual stream size — are rejected
  with the framework's 413 `REQUEST_TOO_LARGE` before validation, and
  operations with request bodies document the 413 in OpenAPI.
  **Behavior change on upgrade**: existing apps gain the 1 MiB default
  cap; pass `max_request_bytes=None` to keep unlimited bodies. Clients
  that abandon an upload mid-stream now log at info (499), not as
  unhandled 500s.
- Route lifecycle on the wire: `contract(deprecated=...)` accepts an
  aware datetime and sends an RFC 9745 `Deprecation: @<unix-timestamp>`
  header (plain `True` sends the legacy `Deprecation: true` form), and
  the new `contract(sunset=...)` (aware datetime) sends an RFC 8594
  `Sunset` header and an `x-sunset` OpenAPI extension, both normalized
  to UTC.
- `tenchi routes --json`: the route table as a machine-readable app map
  (method, path, status, use case, errors, tags, lifecycle).
- `docs/read-replicas.md`: the read/write-splitting recipe — staleness
  tolerance as a port-level contract, `AsyncExitStack` wiring for
  multi-resource request scopes, structural read-your-writes, and
  hook-based post-write stickiness. Demonstrated in the taskboard: the
  new `TaskSearch` port runs on a read-only second connection, with
  tests pinning that the read side sees only committed data and
  rejects writes.
- `ExecutionError` (a `TypeError` subclass): every way an `execute()`
  call can be miswired — missing or positional-only parameters, extra
  required parameters, unannotated or unresolvable `request`
  annotations, unusable context sources — raises one deterministic,
  distinctly catchable type, so queue entrypoints can dead-letter
  miswiring instead of retrying it.

### Changed

- The taskboard worker validates payloads through `execute()`; its job
  registry is now just names to use cases. Deterministic failures —
  invalid payloads, miswired handlers, `AppError` rejections — are
  dead-lettered after rolling back the job's transaction, so a poison
  job can neither starve the queue behind it nor commit partial writes
  alongside its dead-letter record.
- `execute()` checks signatures eagerly with the same rules as
  `route()` (`**kwargs` use cases now accepted, positional-only and
  extra-required parameters rejected before the context opens),
  resolves only the `request` annotation (so `TYPE_CHECKING`-only
  context annotations work), honors a defaulted `request` parameter,
  and rejects sync context managers and bare async generators as
  context sources instead of passing them through unscoped.
- `tenchi doctor` treats `tenchi.execution` as runtime: domain code and
  use cases must not import it — running use cases is entrypoint work.
  Root re-exports (`from tenchi import execute` / `create_app` /
  `Client`) are now caught the same as their submodule spellings.

## [0.5.0] - 2026-07-14

### Added

- `ROADMAP.md`: the lane statement, the anti-roadmap (what Tenchi will
  never grow), and the complexity budget every proposal is measured
  against.
- `docs/events.md`: the design decision for events and background work —
  direct effects are ports, deferred effects go through a transactional
  outbox port, workers are ordinary entrypoints; no event bus or job
  runtime in the framework.
- API snapshot guard: `tests/api_snapshot.txt` records every public
  module, signature, field, and constant, and
  `tests/test_api_snapshot.py` fails when the surface drifts from it.
  Intentional changes regenerate the snapshot
  (`TENCHI_UPDATE_API_SNAPSHOT=1 uv run pytest
  tests/test_api_snapshot.py`) so API changes are always visible in
  review.
- The taskboard example demonstrates `docs/events.md` end to end:
  `add_project_member` enqueues a `member_added` job through an
  `Outbox` port on the request's transaction (commit persists state and
  job atomically; rollback discards both), and the worker entrypoint
  (`app/server/worker.py`) validates payloads at the boundary,
  delivers notifications through an ordinary use case, and dead-letters
  unknown or malformed jobs.

### Changed

- README status section now reflects the providers decision and points
  at the roadmap instead of promising provider-backed infrastructure.
- `health_route` gained `check_timeout` (default 5 seconds): an async
  check that hangs now fails as `TimeoutError` and produces the promised
  503 instead of hanging the health endpoint.
- `openapi_route` gained `tags` (default `("docs",)`) so authentication
  hooks can exempt the document route by tag instead of hardcoding its
  path.
- `Client.call` now raises `TypeError` when passed an input the contract
  does not declare (previously silently dropped), raises `TypeError`
  for non-JSON request media types with non-`str`/`bytes` request types
  (previously silently sent JSON), and rejects empty path parameter
  values. Declared error headers (such as `Retry-After`) are now carried
  on the raised `AppError`.
- `route()` now fails at import time when a params model's fields do not
  match the path template's parameters, or when a path declares
  parameters without a params type — such routes could never succeed.
- `route_group` rejects prefixes ending in `/` (they built double-slash
  paths that never matched).
- `openapi_schema` raises on duplicate method+path pairs and on
  conflicting component schemas (two different models sharing a class
  name, or one model whose validation and serialization schemas
  diverge) instead of silently documenting the wrong schema.
- `tenchi doctor` got stricter: every non-test module under `app/` is
  parsed (syntax errors in server or unclassified files were previously
  invisible), unrecognized feature modules are findings, shared code
  may not import feature policies, the `# doctor: public` pragma only
  counts as a real comment, only `context.user` (not any `.user`)
  counts as an authorization reference, and `from app.server import
  context` now resolves per imported name instead of being flagged.
- CLI generators reject Python keywords as names (`tenchi make use-case
  todos return` previously generated unparseable code) and validate the
  feature argument.
- The roadmap's complexity budget gained a sixth rule: adversarial
  review before each release, with findings verified by repro before
  they are believed.

### Fixed

- Request validation failures from models with custom validators now
  return 422 as promised; previously the validator's exception object
  leaked into the error details and crashed serialization into a 500.
- A use-case result that fails response validation now raises through
  the request-scoped context, so the request's transaction rolls back
  instead of committing behind the 500.
- Query models with sequence fields (`tags: list[str]`) now accept a
  single occurrence (`?tags=a`); previously only zero or repeated
  occurrences validated.
- `AppError` details that are not JSON-native (datetimes, Decimals) are
  coerced to JSON-safe values instead of crashing the error response
  into an unhandled 500 without a request id.
- 405 responses keep the `Allow` header; HTTPException headers from
  middleware are preserved; the catch-all exception handler now carries
  the request id.
- `tenchi.testing` lifespan driver: startup failures chain the original
  exception, and stuck apps fail after a timeout with a diagnostic
  instead of hanging the suite.
- Taskboard: task updates now require write ability (members could
  previously modify tasks through the view policy), the task list shows
  member-visible tasks (it disagreed with `get`), outbox claiming is a
  single atomic UPDATE so concurrent workers cannot double-deliver, the
  worker loop survives job failures, connections enforce foreign keys
  and set a busy timeout with WAL enabled, and `member_added` payloads
  are self-contained (delivery no longer re-reads state that may have
  changed since enqueue). The todos API-key hook compares keys in
  constant time.

## [0.4.0] - 2026-07-14

### Added

- `tenchi.testing`: `open_client` and `open_http` — in-process test
  clients that drive the app's ASGI lifespan themselves, removing the
  need for `asgi-lifespan` in application test suites.
- `tenchi.pagination`: generic `Page[Item]` response model, `PageQuery`
  base for filterable list queries, and the `page()` constructor.
- `tenchi.health`: `health_route(checks=...)` — a health endpoint served
  through Tenchi's own route machinery; checks receive the app context,
  and failures map to a 503 `UNHEALTHY` envelope exposing exception class
  names only. Tagged `health` for hook exemption.
- `tenchi doctor` authorization consistency check: in an app where any
  use case references authorization (`require_user`, `context.user`, or a
  policy import), use cases that reference none are flagged unless marked
  with `# doctor: public` — a forgotten authorization check becomes a
  finding instead of an open endpoint. Apps with no authorization anywhere
  are left alone.
- The policies convention: `features/<feature>/policy.py` holds business
  authorization as plain functions taking their subjects as arguments;
  abilities live with the feature owning the subject. `tenchi doctor`
  gains a policy category enforcing that policies never import
  infrastructure, the app context, or the HTTP runtime, and
  `tenchi make feature` scaffolds `policy.py`.
- `create_app(middleware=...)`: a passthrough seam for Starlette
  middleware (CORS, compression, trusted hosts) — Tenchi composes the
  list into the underlying Starlette application without wrapping or
  re-exporting anything.
- Request ids: every response carries an `x-request-id` header — the
  inbound header when the client sends one (up to 200 characters),
  otherwise a generated UUID hex. Error envelopes include the id as
  `request_id`, hooks see it on `RequestInfo.request_id`, and server-side
  error logs are stamped with it, so a client-reported failure can be
  matched to its log line.
- OpenAPI security schemes: `openapi_schema` and `openapi_route` accept
  `security={"bearerAuth": {"type": "http", "scheme": "bearer"}}`-style
  declarations. Schemes land in `components.securitySchemes` and apply
  globally; operations whose tags intersect `public_tags`
  (default `("health",)`) are exempted with an empty security list,
  matching the hook-exemption convention.

## [0.3.0] - 2026-07-14

### Added

- Request-scoped contexts: `create_app`'s context factory may return an
  async context manager (typically an `@asynccontextmanager` function),
  entered at request start and exited at request end. Hook and use-case
  exceptions flow through `__aexit__` before the error response is built,
  so per-request transactions commit on success and roll back on error.
  The taskboard example now opens a connection and transaction per
  request this way. `docs/providers.md` records the accompanying
  decision: Tenchi documents ports + adapters + scoped resources as its
  integration story instead of growing a provider-package tier.

- `Client` owns more of its transport: `Client(headers=...)` sends default
  headers on every request (the natural home for an `authorization`
  header), and `Client(transport=...)` constructs an owned client over any
  httpx transport — `Client(transport=httpx.ASGITransport(app=app))` makes
  in-process test clients one-liners with no separate httpx lifecycle to
  manage. `http=` remains for fully caller-configured clients and is now
  mutually exclusive with the other transport options.

- `Client(errors=...)`: client-level expected errors, the counterpart of
  `route_group(errors=...)` for errors the server's hooks may raise on
  any route. Discovered by the taskboard stress-test app: group-declared
  errors exist on amended contract copies inside the group, so a client
  calling with the original contract constants had no way to type them.
- `examples/taskboard/`: the stress-test application — projects and tasks
  with cross-feature validation, bearer-token authentication, ownership
  rules in use cases, pagination, partial updates, and SQLite adapters
  sharing one lifespan-managed connection. Runs as a standalone uv
  project with its own CI checks.

## [0.2.0] - 2026-07-14

### Added

- `tenchi doctor`: static dependency-direction and structure checks.
  Imports across `app/` are resolved (including relative imports) and
  validated against the architecture rules — use cases cannot import
  concrete infrastructure or the HTTP runtime, schemas and ports stay
  runtime-free, shared code cannot depend on features, infrastructure
  cannot reach back into use cases, routes, contracts, or server
  composition. Findings carry file, line, and the rule broken.
- `headers=` on contracts: request headers validated into a model and
  passed to the use case, completing the input surface alongside body,
  path, and query. HTTP names map to fields by lowercasing and swapping
  `-` for `_`; the typed client and the OpenAPI document reverse the
  mapping.
- `create_app(hooks=...)`: the authentication seam. Hooks receive a
  `RequestInfo` (method, path, lowercased headers, matched contract) and
  the request context, run before input validation, and either raise an
  `AppError` to reject or return an enriched context to attach identity.
- `route_group(errors=...)`: declare expected errors across every
  contract in a group — the ergonomic way to declare hook-raised errors,
  which also documents them on every route in the OpenAPI document.
- `tenchi.server.RequestInfo` exported from the package root.
- The todos example wires an optional API-key hook (`TODOS_API_KEY`).

## [0.1.0] - 2026-07-14

Initial release.

- Contracts defining and validating the HTTP boundary: JSON bodies, path
  and query parameters, success status, declared errors, documentation
  metadata, and non-JSON media types.
- Routes binding contracts to plain async use-case functions with
  import-time signature validation.
- An ASGI server (`create_app`) with request-scoped context creation,
  lifespan-managed resources, expected-error mapping, and a standard
  error envelope distinguishing framework-owned from app-owned errors.
- Protocol-based ports with memory and SQLite adapters in the todos
  example.
- A contract-driven typed `httpx` client.
- OpenAPI 3.1 generation from contracts, served through the framework's
  own route machinery.
- The `tenchi` CLI: `new`, `make feature`, `make use-case`, `routes`,
  `openapi`, `dev`.
