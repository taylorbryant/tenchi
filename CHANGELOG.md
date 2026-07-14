# Changelog

All notable changes to Tenchi are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and Tenchi adheres to
[Semantic Versioning](https://semver.org/) with pre-1.0 semantics: minor
versions may change the public API.

## [Unreleased]

### Added

- Request-scoped contexts: `create_app`'s context factory may return an
  async context manager (typically an `@asynccontextmanager` function),
  entered at request start and exited at request end. Hook and use-case
  exceptions flow through `__aexit__` before the error response is built,
  so per-request transactions commit on success and roll back on error.
  The taskboard example now opens a connection and transaction per
  request this way. `docs/providers.md` records the accompanying
  decision: Tenchi documents ports + adapters + scoped resources as its
  integration story instead of growing Beignet-style provider packages.

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
