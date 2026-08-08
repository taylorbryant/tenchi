# Tenchi framework audit — 2026-08-08 (v0.12.0)

A deep-dive audit of the framework against its two stated goals:

1. make it easy for coding agents to **build** backend APIs, and
2. make it easy for agents to **consume** those APIs.

Method: five parallel deep reviews (core HTTP boundary; machine-consumption
surface; operational/durability surface; CLI and tooling; examples, docs, and
packaging), each reading source and tests and reproducing suspected bugs by
execution. Findings labeled **confirmed** were reproduced with running code;
**suspicion** means traced in code but not executed.

## Baseline

Everything the repo gates on is green at HEAD:

- root project: `ruff check` and `ruff format --check` clean, Pyright strict
  0 errors, **922 tests passing**
- `examples/taskboard`: 87 tests passing, Pyright clean, `tenchi doctor` clean

The findings below are therefore all things the existing gates do not see.

## Follow-up status

The changes following this audit resolve H1 through H7, plus the
bare-OpenAPI metadata divergence, machine-readable stdout contamination, and
orphaned check subprocess findings. Regression coverage now exercises each of
those paths, including non-`BaseModel` repeated query fields. Follow-up
adversarial passes also cover path-parameter shapes, concrete HTTP
parameter round trips and inherited defaults, unstable idempotency serializers,
process-group descendants that ignore graceful termination, and bounded
numeric and HTTP-date `Retry-After` hints. Application MCP failure results now
also set the standard MCP `isError` signal. Generated OpenAPI now enumerates
exact error codes and their application/framework source and documents the
framework-owned internal 500 on every operation. The other medium- and
low-severity findings remain future work; the detailed sections below preserve
the evidence captured at the audited revision.

## Verdict in one paragraph

Tenchi is an unusually disciplined codebase whose agent-legibility machinery
(canonical structure enforced identically by AGENTS.md, `doctor`, `map`, the
scaffold, and both examples; snapshot-guarded protocols; `verify --base-ref`
as a machine-checkable completion receipt) is genuinely ahead of anything
comparable. Goal 1 — agents *building* APIs — is largely achieved, with the
remaining pain concentrated in a handful of CLI inconsistencies and
composition-time checks the framework promises but misses. Goal 2 — agents
*consuming* APIs — is achieved only for MCP and in-ecosystem Python clients;
the raw-OpenAPI path, which is exactly the path an external agent takes, is
honest but under-specified (no examples, no machine-readable error taxonomy,
no idempotency surfacing, no streaming story). Seven confirmed high-severity
defects need fixing; none is architectural.

---

## High-severity findings (all confirmed by execution)

### H1. Singular contract accepts `status=204/205/304` with a response body; the server emits a 204 with a body

`src/tenchi/contracts.py:337-338` only range-checks `status`;
`src/tenchi/server.py:936-950` then serializes the payload.
`contract(method="GET", path="/gone", response=Item, status=204)` composes,
and at runtime returns a 204 carrying a JSON body — forbidden by RFC 9110
§6.4.1. Over uvicorn/h11 this raises `LocalProtocolError` and kills every
request to the route **in production while in-process ASGI tests stay
green**; on laxer stacks the stray bytes can desynchronize HTTP/1.1
keep-alive. The multi-response path already enforces exactly this rule
(`src/tenchi/responses.py:204-207`) — the singular path misses it, a direct
violation of "fail at composition time".

### H2. `fingerprint()` silently collides NaN/Infinity with `null` for untyped and `Any`-typed values

`src/tenchi/idempotency.py:300-338`. The `allow_nan=False` guard runs on the
JSON dump *after* Pydantic has already converted NaN/Inf to `None`
(`ser_json_inf_nan='null'`) for `Any`-typed serialization — which is the
default path for plain dicts/lists and `Any` model fields. Reproduced:
`fingerprint({"x": float("nan")}) == fingerprint({"x": None})`. Consequence:
a reused idempotency key with genuinely different input replays the stored
result instead of raising the mandated `IDEMPOTENCY_CONFLICT` — the exact
false-replay the fingerprint exists to prevent. Typed paths are safe, which
makes the optional `annotation=` parameter the invisible load-bearing safety
feature. Related low: annotation-less mode also collides `b"abc"` with
`"abc"` and `datetime` with its ISO string (`idempotency.py:308`).

### H3. Typed client sends Python `repr()` for nested query objects and non-scalar header fields

`src/tenchi/client.py:881-893` (query), `:942-962` (headers, `str(value)`).
Reproduced: a nested query model field goes on the wire as `flt={'a': 1}`;
a `list[str]` header field as `x-things: ['a', 'b']`. The composition-time
preflight (`client.py:1207-1234`) only checks the top level is
object-shaped, so these contracts pass `tenchi check`, publish OpenAPI
parameters no client can satisfy (`openapi.py:635-658` emits no
`style`/`content` encoding), and 422 on every real call. Composition-
detectable defect surfacing at request time.

### H4. Server-controlled `Retry-After` overrides `max_delay_seconds` unbounded

`src/tenchi/client.py:1136-1147` — `max(exponential, retry_after)` ignores
the policy's declared delay ceiling. Reproduced: a declared error carrying
`Retry-After: 86400` sleeps the client for a day despite
`max_delay_seconds=1.0`. A remote server (or any intermediary that can
inject the header) can pin calling workers arbitrarily; the only defense is
`total_timeout_seconds`, which is off by default. Clamp `retry_after` to the
policy bound or an explicit `max_retry_after_seconds`.

Resolved after this audit: both numeric delays and HTTP dates are now clamped
to `RetryPolicy.max_delay_seconds`, and the bounded value is exposed to attempt
observers.

### H5. Additive changes to nested tool schemas can never pass the compatibility gate

`src/tenchi/_schema_compatibility.py:31-49` omits `$defs` from
`_SCHEMA_KNOWN`, so any change beneath it — including a purely additive
optional property on a nested model — is *also* reported as
`unsupported schema keywords changed` → `unknown` → incompatible.
Reproduced: `tools --diff` exits 1 and `verify` returns `ok=false` for an
additive change AGENTS.md:328-332 says must pass. OpenAPI is immune only
because `openapi.py:684-692` relocates `$defs` into `components/schemas`.
For any tool with nested models, the documented additive workflow is
impossible.

### H6. `tenchi doctor` crashes with a raw traceback on non-UTF-8 or unreadable app source

`src/tenchi/doctor.py:322-333` catches only `SyntaxError` around
`read_text(encoding="utf-8")`; the authorization pass (`:386`) catches
nothing. Reproduced: one latin-1 byte in any `app/**.py` →
`UnicodeDecodeError` traceback from `doctor`, the `doctor` step of `check`,
`map`, `verify`, and the MCP `doctor` tool. Should be a structured
`TENCHI_DOCTOR_*` finding like the existing syntax-error path.

### H7. `docs/content/testing.mdx` documents a generated-app OpenAPI test that fails when copied

`docs/content/testing.mdx:139-148` shows
`main(["openapi", "--check", "openapi.json"])` without the
`--routes app.server.routes:api_routes --title … --version …` flags the real
scaffold test passes (`src/tenchi/scaffold.py:682-711`). Reproduced in
`examples/todos`: the snippet fails (extra `/health`, `/docs`,
`/openapi.json` operations; title mismatch). An agent copying the docs gets
a permanently red test. Root cause is H8/M-class finding below: bare
`tenchi openapi` alone resolves metadata differently from `check`/`verify`.

---

## Medium-severity findings

### Boundary correctness (confirmed unless noted)

- **Non-`BaseModel` query types break single-item list coercion.**
  `server.py:827-835` returns sequence fields only for `BaseModel` query
  types; a dataclass query with `tags: list[str]` 422s on `?tags=a` but
  succeeds on `?tags=a&tags=b`. Contradicts the documented "any type
  Pydantic can validate" claim; also `validation_alias` handling only honors
  plain-`str` aliases. (`server.py:803-835`)
- **Required-but-nullable response header always 500s.** Composition accepts
  nullable header schemas (`contracts.py:83, 678-732`) but
  `_validated_response_headers` dumps with `exclude_none=True` then errors
  on the missing key (`server.py:907-933`). Projector returns
  `{"X-Note": None}` → framework 500.
- **`health_route()` performs no composition-time validation.**
  Non-callable checks and non-positive `check_timeout` compose and turn the
  endpoint into a permanent 503; the timeout covers only async checks, so a
  blocking sync check hangs the endpoint; a `BaseException` from a check
  becomes a framework 500. (`health.py:51-100`)
- **Declared `AppError` raised in a presenter/projector → 500 with a
  misleading "response does not match contract" log.**
  (`server.py:1257-1298`)
- **Trailing-slash requests get a bare Starlette 307** (no envelope, no
  `x-request-id`, no observer) via inherited `redirect_slashes=True`.
  (`server.py:461-469`)

### Operational surface

- **Job messages have no versioning or compatibility gate** — the one
  artifact that routinely outlives a deploy. `JobMessage` is name +
  payload only (`jobs.py:128-158`); consumer-side schema skew surfaces as a
  raw `pydantic.ValidationError` from `dispatch` (`jobs.py:242-274`) with no
  Tenchi-typed signal a queue adapter can classify, and `verify` checks
  OpenAPI/tool/evaluation snapshots but not job schemas.
- **The recommended same-transaction idempotency wiring makes
  `IDEMPOTENCY_IN_PROGRESS`, `Retry-After`, `reservation_ttl`, and
  `abandon` effectively unreachable** — a reserved row is never visible
  outside the uncommitted transaction, so concurrent duplicates block on the
  DB lock (SQLite: `OperationalError` → 500 past `busy_timeout`;
  Postgres: unbounded wait holding a pool connection) instead of receiving
  the polite 409. The module documents both behaviors without saying they
  are mutually exclusive per wiring (`idempotency.py:9-11, 45-51, 109-116`;
  `examples/taskboard/app/infra/sqlite_idempotency.py`). The state machine
  itself is sound, and the same-transaction claim was verified end-to-end
  through the taskboard — the gap is documentation and contract-declaration
  guidance.
- **One unrelated case failure in a budgeted evaluation aborts the whole
  suite as `EVALUATION_BUDGET_UNVERIFIED`.** Reproduced: 3-case suite,
  case 0 raises → cases 1-2 never run, labeled as a budget failure
  (`evaluations.py:685-702, 868-891`). Fail-closed is defensible; the
  amplification and mis-attribution are surprising and untested.
- **Use-case observers report `succeeded` when job/task result validation
  then fails and the unit of work rolls back.** Reproduced
  (`execution.py:109-139`, `jobs.py:264-274`, `tasks.py:217-230`). Worker
  alerting built on `UseCaseObserver` misses deterministic result-contract
  failures.

### Machine-consumption surface

- **Application MCP failures return `isError: false`.** All failure
  envelopes — including denied destructive calls — are successful
  `CallToolResult`s with `ok: false` only inside Tenchi's envelope
  (`mcp.py:877-908`). Generic MCP hosts keying off `isError` feed failures
  to the model as ordinary output.

  Resolved after this audit: failure envelopes remain structured tool results
  and now set MCP's standard `isError` flag.
- **OpenAPI omits the framework 500 and auth failure statuses the runtime
  actually produces** (`openapi.py:461-516`): the honesty rule maps every
  undeclared error to 500, and `security=` adds requirements but no 401/403
  responses.

  Resolved after this audit: every operation now documents the framework-owned
  internal 500. Authentication statuses remain application-owned and appear
  when the hook's errors are declared on the protected contract or route group;
  security-scheme metadata does not invent them.
- **Error taxonomy is not machine-readable in OpenAPI**: one generic
  `ErrorResponse` component with unconstrained `code`; codes live in
  free-text descriptions. The MCP adapter proves the better design
  (per-tool code enums, `mcp.py:813-831`). Worse, the client's own error
  classification requires `x-tenchi-error-source: app`
  (`client.py:993-998`) — a header documented nowhere in the generated
  document, so a client generated from the OpenAPI doc cannot reproduce
  Tenchi's error semantics.

  Resolved after this audit: each error status now narrows `code` to its exact
  values and documents the required `x-tenchi-error-source` header.
- **Transient infrastructure errors are unretryable by policy**: `retry_on`
  accepts only declared app error codes; a load balancer's 503 or any
  framework-sourced 5xx is `UnexpectedResponseError`, never retried
  (`client.py:545-556, 993-998`). No `retry_on_statuses` knob exists.
- **Invalid success bodies raise bare `pydantic.ValidationError`**,
  contradicting the module's documented two-exception taxonomy and leaking
  response payload fragments into logs (`client.py:787-794`), while
  `UnexpectedResponseError.body` retains the raw body and the client drops
  the envelope's `request_id` when raising `AppError`
  (`client.py:99-102, 999-1010`).
- **Application MCP security invariants cover only the Streamable HTTP
  path** (suspicion, surface confirmed): the SDK's inherited
  `sse_app`/`run_sse_async` bypass the stateless enforcement, header
  capture, and the `transport_security` passed at composition
  (`mcp.py:233-294`).

### CLI and tooling

- **`routes --json`, `map --json`, and `openapi` print modes let
  application import-time stdout corrupt the JSON stream.** Reproduced: a
  `print()` in `app/server/routes.py` breaks `json.load` on the output.
  Other commands already redirect (`cli.py:1693` etc.); `map` also loads
  the evaluation runner without `discard_evaluation_output`
  (`cli.py:1529-1588, 1875-1929`).
- **Ctrl-C during `check`/`verify` orphans the running step subprocess**
  (started with `start_new_session=True`, so the terminal's signal never
  reaches it, and no `finally` stops it — `_checks.py:171-264`). Pytest
  keeps running detached. Fix: `finally: _stop_process(process)`.
- **`doctor` calls structure optional that `map`/`check`/`verify` hard-
  require.** Deleting `app/server/tools.py` from a fresh scaffold: doctor
  clean, `map --json` exits 1 with an unstructured import error
  (`doctor.py:39-46`, `_checks.py:131-161`).
- **Doctor false negatives**: feature `__init__.py` and stray top-level
  `app/*.py` modules escape every dependency rule (reproduced —
  `doctor.py:534-578, 343-364`); dynamic imports are inherently invisible
  to the AST pass (worth documenting).
- **Most `--json` commands have unstructured error paths**: target-loading
  failures print one plain stderr line and exit 1 with no versioned JSON
  error object — the most common failure class for an agent editing the
  app (`cli.py:1293-1295, 1536-1539, 1562-1582, 1602-1604, 1696-1698`).
  `tenchi new` also lacks `--json`/`--dry-run` (contrast `make`).
- **Bare `tenchi openapi` resolves metadata differently from
  `check`/`verify`/MCP** — directory-name title, `0.1.0` version, full
  route group — so the natural `tenchi openapi --write` produces a snapshot
  the same CLI's `check` then fails (`cli.py:464-477, 1897` vs
  `_cli_operations.py:364-399`). This is the root cause of H7 and the
  sharpest CLI edge for agents; either make `openapi` discover `OPENAPI_*`
  literals like `check` does, or document the divergence in `cli.mdx`.
- **AGENTS.md drift**: the module list omits `responses.py`, `tasks.py`,
  `preflight.py`, and misattributes ownership of `AGENT_PROTOCOL_VERSION`
  (lives in `_cli_results.py:42`); "todos demonstrates each capability
  once" overclaims (its tasks/jobs/tools/evaluations/preflight compositions
  are deliberately empty); the verification checklist predates
  `check`/`verify` and no longer matches CI.
- **Root CI base-ref edge**: on a branch's first push or force-push,
  `github.event.before` is the zero SHA and `tenchi verify --base-ref`
  fails outright (`.github/workflows/ci.yml:28-44`).

---

## Low-severity findings (condensed)

- Header aliases containing literal underscores are silently re-spelled to
  hyphens, so a valid `x_literal_underscore` header 422s
  (`server.py:742`; contradicts `contracts.py:229-233`).
- Reserved-header lists asymmetric: `ErrorDef` may declare `Set-Cookie` /
  `Deprecation` / `Sunset`; success contracts may not
  (`errors.py:23-36` vs `contracts.py:64-82`).
- Repeated `x-request-id`: envelope uses first value, `RequestInfo.headers`
  keeps last (`server.py:704-721`).
- Context factory with all-defaulted params silently treated as zero-arg —
  lifespan state never passed (`server.py:472-490`).
- `application/json; charset=utf-16` passes the media check but decodes as
  UTF-8 → confusing 422 (suspicion; `server.py:1206-1207`).
- Committed-then-504 on suppressed deadline cancellation is deliberate but
  should point at idempotency in docs; passthrough mutates the presenter's
  `Response` object; observers add unbounded response latency outside
  `contract.timeout` (`server.py:985-1042, 1328-1398`).
- Operations outliving `reservation_ttl` (default 300 s, undocumented at
  the call site) permit a duplicate concurrent execution window;
  `completed_ttl=None` default retains replays forever.
- Rate-limit/idempotency conformance suites: only certifiable by
  clock-injectable adapters (a real Redis adapter cannot pass); no
  too-early-expiry negatives, so an adapter expiring at half TTL passes;
  the suites never exercise the same-transaction composition mode the docs
  recommend.
- Tasks have no timeout facility at all — `tenchi task run` can hang
  forever, unlike preflight/evaluations (`tasks.py:12-14`).
- `list_tools` auth failures surface as JSON-RPC `INTERNAL_ERROR`
  (`mcp.py:296-305`); OTel marks declared app errors `StatusCode.ERROR`,
  inflating error rates (`opentelemetry.py:319-322`).
- `RetryPolicy` requires `max_attempts >= 2`, so `total_timeout_seconds`
  can't express a one-shot deadline; OpenAPI `operationId` disambiguation
  is route-order-dependent (`retries.py:44-51`, `openapi.py:704-708`).
- `Page`/`page()` accept negative/inconsistent `total`; no `has_more`, no
  cursor mode, no client iterator (`pagination.py:35-48`).
- `tenchi new` is non-transactional (partial dir on mid-scaffold failure);
  `make`'s staged-write machinery isn't reused (`cli.py:995` vs
  `_cli_operations.py:296-324`).
- Loaders permanently mutate `sys.path`/`sys.modules` (compensated in MCP
  via `isolated_project_imports`, not for public `route_map()`); pathlib
  symlink traversal differs between Python 3.12 and 3.13+ (doctor/map may
  skip symlinked dirs on some interpreters); `render_openapi_snapshot`
  omits `allow_nan=False`; `_contract_record_matches` uses `endswith`
  path matching; baseline OpenAPI version pinned to exactly `"3.1.0"`.
- Scaffold: generated apps pin no tenchi version but ship
  generator-rendered snapshots (version-skew risk on `uv sync` against
  PyPI); dead `todo_not_found` ErrorDef never wired; no `CLAUDE.md` shim
  (the repo itself keeps one because some harnesses load it by name);
  `OPENAPI_DESCRIPTION` drift between scaffold and `examples/todos`.
- Docs/CI nits: getting-started scaffold table omits `app/server/routes.py`
  and `app/shared/errors.py`; framework error codes (`VALIDATION_ERROR`,
  `UNSUPPORTED_MEDIA_TYPE`, …) are enumerated nowhere; release workflow's
  taskboard leg skips Ruff; taskboard health check constructs a synthetic
  `OwnerScope("__health__")`, modeling the exact bypass the type exists to
  prevent.

---

## Goal 1: agents building APIs — assessment

**Largely achieved, and the infrastructure is real.** `tenchi new` emits an
app that passes an 8-step CI-grade gate untouched, including a genuinely
good generated AGENTS.md and `.mcp.json` wired to structured
`app_map`/`check`/`verify`/`*_diff` tools with versioned, snapshot-guarded
schemas. The inspect → preview (`--dry-run`) → edit → validate → diff →
receipt (`verify --base-ref`) loop is described identically in README,
docs, scaffold, and MCP instructions — that coherence is the repo's biggest
strength, and `verify --base-ref` as a machine-checkable "definition of
done" tied to an immutable commit is a standout idea. Drift is engineered
against (docs code blocks validated against the API snapshot; six immutable
agent-protocol snapshots), which is why a 37-page docs audit surfaced only
one executable inaccuracy (H7).

Remaining friction, in order of pain: the `openapi` metadata ceremony
(H7 + CLI divergence above — the project's own docs got it wrong; an agent
will too); `$defs` making the documented additive-tool workflow impossible
(H5); unstructured error paths on the `--json` surface; the empty starter
compositions with no per-capability pointer into the taskboard ("see
`examples/taskboard/app/server/hooks.py` for the bearer pattern" in the
generated AGENTS.md would cut agent search cost substantially); and the
two-MCP-servers naming hazard (`tenchi mcp` vs `tenchi.mcp`), which the
docs handle but the generated AGENTS.md addresses in one sentence.

## Goal 2: agents consuming APIs — assessment

**Achieved for MCP; strong for in-ecosystem Python; only adequate for the
raw-OpenAPI path — which is the path an external agent actually takes.**

The application MCP manifest is the best artifact on this surface: per-tool
JSON Schemas, declared error codes as enums, conservative safety
annotations (destructive defaults true for writes), auth on discovery and
invocation, fail-closed approval. The typed `Client` is best-in-class when
the consumer can import the server's contract objects — eager preflight
before I/O, symmetric error semantics, payload-safe observability, bounded
retries.

But a non-Python or third-party agent gets only the OpenAPI document, and
today that document is not sufficient to reproduce correct behavior:

1. **No examples anywhere** — `contract()` has no `examples=`; for LLM
   consumers one example request/response per operation is worth more than
   most of the schema.
2. **No `servers` block** — the document doesn't say where the API lives.
3. **Error codes not machine-discoverable; `x-tenchi-error-source`
   undocumented** — the doc cannot teach a consumer Tenchi's own error
   protocol, which the typed client itself depends on.
4. **Auth is a bare scheme object**; no 401/403 responses, no 500.
5. **No streaming story** — contracts are buffered request/response only;
   no SSE/chunked modeling, which agent-facing APIs increasingly need.
6. **Webhooks**: inbound signed contracts get only a boolean
   `x-tenchi-webhook` extension; signature scheme, header names, and replay
   rules — everything a consumer must implement — are undocumented, and
   OpenAPI 3.1's native `webhooks` section is unused.
7. **Pagination is structural, not semantic** — nothing marks
   `limit`/`offset`/`total` as a convention; no client iterator.
8. **Idempotency is invisible** — the server has a full idempotency
   subsystem, but nothing in OpenAPI advertises which operations accept a
   key or what header carries it, and the client won't generate one on
   unsafe retries. This is the single most important thing an agent needs
   to retry POSTs safely.

There is also no `tenchi` command to emit a self-contained client or a
consumption guide companion to the doc — an `llms.txt`-style artifact *for
the built application* (the docs site has one for the framework itself).

## Priorities

1. **Correctness fixes (small, high value):** H1 (reject 204/205/304 with
   a body at `contract()`), H2 (run the NaN scan on the python-mode dump or
   set `ser_json_inf_nan` to reject), H3 (reject non-scalar query/header
   fields at composition until an encoding exists), H4 (clamp
   `Retry-After`), H5 (add `$defs` to `_SCHEMA_KNOWN` and compare it
   structurally), H6 (structured doctor finding for undecodable files).
2. **CLI coherence:** make bare `tenchi openapi` discover `OPENAPI_*`
   literals like `check`/`verify` (fixes H7's root cause); redirect app
   stdout in `routes`/`map`/`openapi`; structured JSON error objects on
   every `--json` failure path; `finally`-stop the check subprocess.
3. **Close the consumption gap:** contract-level `examples=` flowing into
   OpenAPI and the MCP manifest; enumerate error codes per operation as
   schema enums; document `x-tenchi-error-source`, the 500, and auth
   failures in the generated doc; surface idempotency-key headers; add
   `retry_on_statuses` for transient infrastructure errors; set
   `isError: true` on MCP failure envelopes.
4. **Document the honest trade-offs:** the two idempotency wirings and
   which errors are reachable in each; `reservation_ttl`/`completed_ttl`
   consequences; the conformance suites' clock-injection requirement;
   job-message evolution guidance (and eventually a job-schema
   compatibility gate in `verify`).
5. **Teach the agent path harder:** per-capability example pointers in the
   generated AGENTS.md; a `CLAUDE.md` shim in the scaffold; enumerate the
   framework error-code table in `errors.mdx`; fix `testing.mdx` (H7).

## Test-coverage gaps worth adding (highest-leverage first)

- Wire-encoding tests for non-scalar query/header fields and
  non-`BaseModel` boundary types (would have caught H3 and the dataclass
  coercion bug).
- Singular-contract `status=204` + body rejection (H1).
- Nested/`Any`-typed NaN fingerprint collisions (H2).
- Additive nested-`$defs` tool-compatibility change must pass (H5).
- stdout-purity tests for `routes --json`/`map --json`/`openapi` with a
  printing app; structured-error-shape tests for `--json` failure paths.
- A framework-level test running `run_idempotently` with a store sharing
  the operation's transaction where the operation fails mid-write — the
  mode the docs recommend is never exercised by the framework suite.
- Retrying an unsafe method with `allow_unsafe_methods=True`; HTTP-date
  `Retry-After`; paths with spaces/non-ASCII; `make feature` output run
  through full `tenchi check` in CI (AGENTS.md requires it; CI only
  import-smokes it).

## Strengths worth preserving

- **Honesty rule enforced uniformly** across use cases, hooks, webhook
  verifiers, and context factories — all tested.
- **Defense-grade boundary hardening**: overflow-hardened Content-Length
  parsing with counted-stream fallback; a single funnel for every outbound
  header path with CRLF/control tests on each; no input echo in validation
  errors; client disconnects handled as routine traffic.
- **Webhook ordering proven correct**: hooks → size/media guards →
  exact-bytes verifier → parser, so unverified input never reaches an
  error-disclosing parser.
- **Fail-closed bias throughout the operational surface**: token-fenced
  completion, replay-bytes validation, budget overflow → fail-closed,
  deadline re-checks that a `CancelledError`-eating check can't defeat,
  runtime store-shape validation that makes a subtly-wrong adapter fail
  loudly.
- **The taskboard is an honest stress test** — outbox with a correct
  three-way worker failure taxonomy, optimistic concurrency, idempotency
  nested around rate-limit consume, read-only preflight — with
  teaching-quality comments.
- **Drift is engineered against**: API snapshot, six immutable
  agent-protocol snapshots, docs code blocks validated in tests, example
  snapshots CI-compared against historical baselines.
- **`verify --base-ref` as a completion receipt** — including fail-closed
  missing-baseline semantics with an explicit human-authorized
  first-adoption override — is a genuinely novel answer to "how does an
  agent prove it's done".
