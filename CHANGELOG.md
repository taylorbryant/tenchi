# Changelog

All notable changes to Tenchi are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and Tenchi adheres to
[Semantic Versioning](https://semver.org/) with pre-1.0 semantics: minor
versions may change the public API.

## [Unreleased]

## [0.8.0] - 2026-07-18

### Added

- A version-aware Next.js documentation site with task-oriented MDX guides,
  responsive navigation, local search, anchored tables of contents, light and
  dark themes, generated `llms.txt` files, and a static GitHub Pages export at
  [tenchi.io](https://tenchi.io/).
  The site now separates the quickstart, mental model, core runtime,
  authentication, testing, operations, comparisons, and module reference into a
  coherent learning path.
- Deterministic application OpenAPI snapshots: `tenchi openapi --write`
  emits canonical, key-sorted JSON and `tenchi openapi --check` fails on drift
  with a semantic summary and unified diff. Description, security schemes, and
  public tags can be supplied so the CLI reproduces the served document. The
  generated starter and both examples now commit and test their snapshots,
  and CI verifies the workflow directly.
- Conservative OpenAPI compatibility reports: `tenchi openapi --diff`
  classifies changes against a committed snapshot as breaking, additive,
  metadata-only, or unknown. Breaking and unknown changes fail closed, while
  `--diff-format json` provides machine-readable output for automation. CI
  compares both examples with their snapshots from the base commit, and the
  generated starter documents the required pre-write workflow. The underlying
  analyzer is available from `tenchi.compatibility` for programmatic checks.
- Explicit public-operation metadata: `contract(public=True)` gives
  authentication hooks and OpenAPI one access-control signal instead of
  overloading documentation tags. `public` defaults to `False`; hooks decide
  how that metadata affects authentication. `health_route()` and
  `openapi_route()` default to public and allow `public=False`. The JSON route
  map now exposes the value.

### Changed

- Official Python support now covers 3.12, 3.13, and 3.14, with the complete
  CI suite running against every supported version.
- The documentation now uses a green visual identity and a lowercase `t` mark
  in both light and dark themes, including its favicon and installable-site
  metadata.
- Dark-mode code blocks now use a custom emerald, sage, and warm-amber syntax
  palette designed for the documentation site's green visual identity.
- Multiple successful responses now have one authoritative declaration:
  `response(Body, status=...)` returns a `ResponseDef`,
  `contract(responses=(...))` derives the aggregate body and header types, and
  `present(definition, body)` selects the wire response. This removes repeated
  aggregate unions while preserving strict presenter and typed-client
  inference. A single response with alternative top-level body schemas uses
  `response(A, B, status=...)`, which Pyright infers as `A | B`; nested unions
  keep their normal spelling. Each response definition declares one fixed
  object-shaped header schema. `ClientResponse.definition` identifies the
  selected definition; singular `contract(response=...)` declarations are
  unchanged.
- Declared media types are authoritative at runtime. Request bodies with a
  missing or mismatched `Content-Type` receive a framework-owned 415
  `UNSUPPORTED_MEDIA_TYPE`, and the typed client rejects successful and error
  responses whose `Content-Type` does not match their declared wire format.
  Charset-qualified text is encoded and decoded strictly in both directions,
  while unsupported declared charsets fail at composition. OpenAPI documents
  the 415 for every operation with a request body.

### Fixed

- Route composition now rejects equivalent templates whose path-parameter
  names differ instead of allowing declaration order to silently choose a
  handler. Runtime route specificity is deterministic, explicit `HEAD`
  contracts take precedence over Starlette's implicit `HEAD` handling, and
  405 responses report every method available at the matching path.
- Documentation examples now show dependency arrows in their true direction,
  a complete repository adapter, and the full relationship between reusable
  authorization abilities and their declared failure semantics. Visibility
  metadata and framework comparisons also use more precise, neutral language.
- The documentation shell now has consistent navigation, search, theme,
  table-of-contents, and code-copying behavior. Search keeps a 16px mobile
  input to prevent iOS Safari focus zoom, and mobile overlays contain their
  scrolling.

### Removed

- The experimental `SuccessDef`, `success()`, `contract(successes=...)`, and
  `ClientResponse.success` response API. Use `ResponseDef`, `response()`,
  `contract(responses=...)`, and `ClientResponse.definition` respectively.
- `openapi_schema(public_tags=...)`, `openapi_route(public_tags=...)`, and the
  CLI's `--public-tag`/`--no-public-tags` options. Set `public=True` on the
  contract instead; documentation tags no longer control security semantics.

## [0.7.0] - 2026-07-16

### Added

- Cancellation-safe request deadlines: `contract(timeout=...)` cooperatively
  cancels overdue route work, waits for request-scope rollback and cleanup, and
  returns a framework-owned 504 `REQUEST_TIMEOUT`. Timed operations document
  the 504 and `x-timeout-seconds` in OpenAPI.
- Named success outcomes and controlled Starlette response passthrough:
  `success()` definitions let one contract declare multiple successful
  statuses, bodies, media types, and header types; a typed synchronous
  `route(..., present=...)` presenter selects the result with `present()`.
  Passthrough outcomes preserve streaming, file, redirect, and background-task
  responses while Tenchi verifies their declared wire metadata. OpenAPI lists
  every outcome, and `ClientResponse.success` identifies and validates the
  status-specific result.
- Request outcome observers: `create_app(observers=...)` invokes sync or async
  observers in order after a matched route's request scope closes. Immutable
  `RequestOutcome` values carry request metadata, status, duration, and error
  ownership for metrics and tracing; observer failures are isolated and
  cannot alter responses.

- Contract-owned successful response headers: declare an object-shaped
  `response_headers=` type on `contract()`, then bind a synchronous, typed
  projector with `route(..., response_headers=...)`. Tenchi validates and
  safely serializes scalar header values before request-scope commit, rejects
  dynamic, undeclared, reserved, and injection-prone headers, and documents
  fixed header fields in OpenAPI. The typed client validates declared headers
  on every call; the new
  `Client.call_with_response()` returns `ClientResponse[Body, Headers]` with
  the validated body, headers, and underlying `httpx.Response` while
  `Client.call()` keeps its body-only API.
- Optimistic concurrency in the taskboard example: task responses carry strong
  `ETag` validators, task creation identifies its resource with `Location`,
  updates require `If-Match`, and both memory and SQLite repositories use
  monotonic versions with atomic compare-and-swap writes. Missing and stale
  preconditions are honest, documented 428 and 412 application errors, and
  existing SQLite task tables migrate safely during concurrent startup.
- Idempotent commands in the taskboard example: task creation requires a typed
  `Idempotency-Key`, atomically creates once across concurrent SQLite requests,
  and replays the original body, `Location`, and `ETag` for matching retries.
  Reusing a key with different input is a declared 409 application error, and
  rolled-back requests do not consume their keys.
- A dependency-free, one-page documentation site covering Tenchi's core
  workflow and framework features, with automatic GitHub Pages deployment.

### Changed

- `UnexpectedResponseError` now accepts and exposes an optional `reason` when
  a response has a declared success status but violates that outcome's wire
  contract.
- The release workflow now reruns the standalone taskboard checks and installs
  the built wheel into a clean environment before generating and validating a
  fresh application, feature, and use-case stub.

### Fixed

- Request deadlines remain authoritative when application code suppresses the
  injected cancellation: a late value can no longer escape as a successful
  response after the deadline has expired.
- Passthrough responses now apply header-injection checks to raw response
  headers, enforce declared media-type parameters such as `charset`, and
  reject streaming or materialized bodies for outcomes that declare no body.
  The typed client likewise rejects non-empty bodies for no-body successes,
  and injection-prone inbound request ids are replaced before being echoed.
- `present()` now preserves each `SuccessDef`'s body and header types for
  Pyright, and aggregate contract response types must exactly match the union
  of their named outcomes instead of advertising impossible client values.
- Observer request headers are now structurally read-only, so one observer
  cannot mutate the `RequestOutcome` seen by later observers.

## [0.6.0] - 2026-07-15

### Added

- The taskboard now separates staleness-tolerant task searches from writes and
  strong reads with a `TaskSearch` port backed by a read-only second SQLite
  connection. Tests cover committed-data visibility and rejected writes.
- `tenchi.execution`: `execute()` runs a use case from any entrypoint —
  worker, script, scheduler, test — with the server's boundary
  guarantees: input validated against the use case's own `request`
  annotation (Python data or raw JSON), undeclared inputs rejected, and
  the same context scoping as `create_app`. `open_context()` exposes
  that scoping directly, and the server now uses it too, so
  commit-on-success / rollback-on-error semantics are defined once.

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
- `ExecutionError` (a `TypeError` subclass): every way an `execute()`
  call can be miswired — missing or positional-only parameters, extra
  required parameters, unannotated or unresolvable `request`
  annotations, unusable context sources — raises one deterministic,
  distinctly catchable type, so queue entrypoints can dead-letter
  miswiring instead of retrying it.

### Changed

- Consolidated the public documentation into a shorter root README and removed
  the separate design notes, roadmap, and example READMEs.
- `route()` now requires explicit annotations for contract-declared boundary
  inputs and the response, and verifies that they exactly match the contract at
  composition time. Contract inputs cannot be hidden behind `**kwargs`, and
  reserved input parameters that the contract does not declare are rejected.
- `execute()` now rejects sync use cases before opening their context, and
  `open_context()` validates that factories take no arguments before invoking
  them. Invalid wiring raises `ExecutionError`; exceptions raised inside a
  valid factory still propagate unchanged.
- Added `TenchiError` and `ConfigurationError` as public exception roots.
  Invalid contracts, route groups, client construction, app assembly, and
  OpenAPI generation now share the configuration type instead of leaking
  plain `ValueError` or Pydantic schema errors. `create_app()` also validates
  lifespan and hook call shapes at composition time. Existing named errors
  (`AppError`, `RouteBindingError`, `ExecutionError`, and
  `UnexpectedResponseError`) now participate in the common hierarchy.
  Error definitions and declaration collections are validated eagerly,
  `AppError` rejects undeclared response headers, and prefixed routes keep
  their default contract names aligned with their rewritten paths. Conflicting
  definitions for one wire error code and unsafe error-header values are also
  rejected before any response is sent. Client calls preflight every declared
  boundary type, malformed schema builders are consistently framed, invalid
  Starlette path syntax is reported as configuration, and contract text
  metadata and OpenAPI document options are type-checked eagerly. The typed
  client also requires the error source header before mapping an envelope to
  `AppError`, so framework-owned failures cannot collide with application
  error codes. Deferred Pydantic adapters are rebuilt during composition and
  rejected there if their forward references remain unresolved. Starlette path
  converters are now substituted by the typed client and normalized in
  OpenAPI while malformed parameter syntax is rejected at declaration;
  multi-scheme security correctly requires every scheme, malformed error
  envelopes remain unexpected responses, and documented error headers are
  deduplicated case-insensitively. Params, query, and header declarations must
  describe object-shaped input across server, client, and OpenAPI; structured
  `+json` media types use JSON encoding, and non-JSON response values that are
  not text or bytes trigger rollback as response-contract violations instead
  of being mislabeled as JSON. Pydantic aliases are now used consistently as
  wire names by the typed client, server response serializer, and OpenAPI.
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

### Fixed

- `contract()` now accepts PEP 604 union annotation objects in its public
  typing, and the typed client distinguishes an omitted request from
  `request=None`, allowing nullable request types to send JSON `null`.
- OpenAPI allocates the framework error-envelope component only after all
  application schemas are known, keeping models named `ErrorResponse`,
  `ErrorResponse_2`, and so on collision-free regardless of route order.
- Repeated contract-declared request headers continue to use the last value,
  including when the header field has a Pydantic alias.
- `AppError` now frames malformed definitions and non-string message overrides
  as `ConfigurationError` instead of leaking incidental attribute errors or
  silently coercing values.

## [0.5.0] - 2026-07-14

### Added

- Established the framework's lane, explicit non-goals, and complexity
  budget for evaluating proposals.
- Defined the approach to events and background work: direct effects are
  ports, deferred effects use a transactional outbox port, and workers are
  ordinary entrypoints rather than a framework job runtime.
- API snapshot guard: `tests/api_snapshot.txt` records every public
  module, signature, field, and constant, and
  `tests/test_api_snapshot.py` fails when the surface drifts from it.
  Intentional changes regenerate the snapshot
  (`TENCHI_UPDATE_API_SNAPSHOT=1 uv run pytest
  tests/test_api_snapshot.py`) so API changes are always visible in
  review.
- The taskboard example demonstrates the transactional outbox end to end:
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
  request this way. Tenchi uses ports, adapters, and scoped resources as
  its integration model instead of growing a provider-package tier.

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
