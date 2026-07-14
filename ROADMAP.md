# Roadmap

This is the document that keeps Tenchi honest. Every "should we add X?"
gets measured against the lane and the complexity budget below; if a
proposal loses that argument, the answer is no even when the feature
would be pleasant.

## The lane

Tenchi is a contract-first framework for typed HTTP APIs in Python. Its
one opinion, applied everywhere: **declare the boundary, validate at the
boundary, and keep everything inside it plain.** Contracts are frozen
dataclasses; use cases are ordinary async functions; dependencies are
`typing.Protocol` ports on a context dataclass; errors must be declared
to be exposed. The framework's job is to make the prescribed architecture
the path of least resistance — and then get out of the way.

Tenchi competes on legibility, not surface area. A developer should be
able to read `src/tenchi/` in an afternoon and hold the whole dispatch
path in their head. When Tenchi and a bigger framework both solve a
problem, Tenchi's solution should be the one you can explain on a
whiteboard without arrows crossing.

## What Tenchi will never grow

The anti-roadmap. These are decisions, not gaps:

- **No dependency-injection container.** The app context is a dataclass;
  wiring is a factory function you can read.
- **No ORM, query builder, or database layer.** Ports in features,
  adapters in `infra/`, resources owned by the lifespan
  (`docs/providers.md`).
- **No provider package tier.** Same note; Python libraries already
  expose context-managed lifecycles, and an adapter is a page of obvious
  code.
- **No decorator-based routing or runtime handler introspection.**
  Contracts and `route()` are explicit values composed in one place.
- **No middleware framework.** `create_app(middleware=...)` passes
  Starlette middleware through untouched; Tenchi wraps and re-exports
  nothing.
- **No plugin or extension system.** Extending Tenchi means writing a
  function that takes routes or contracts as arguments.
- **No settings/config framework.** Composition roots are ordinary
  Python; read your environment however you like.
- **No template engine or server-rendered pages.** Tenchi serves typed
  APIs; pair it with whatever frontend you want.
- **No event bus or background-job runtime.** Side effects are ports;
  workers are entrypoints composing the same use cases
  (`docs/events.md`).

## The complexity budget

Rules that keep the framework small enough to trust:

1. **Four runtime dependencies** — httpx, pydantic, starlette,
   typing-extensions. Growing this list requires a design note
   explaining why an adapter in application code cannot do the job.
2. **Public API is plain values**: functions, frozen dataclasses, and
   Protocols. Nothing in the public surface requires inheritance,
   decoration, or metaclasses to use.
3. **Every abstraction is earned by two real uses.** A capability lands
   only when both example apps (`examples/todos`,
   `examples/taskboard`) — or a real app and one of them — would use it.
   One hypothetical user is not evidence.
4. **Every feature is teachable in one README section.** If explaining a
   capability needs a page of caveats, the capability is wrong.
5. **Update every surface** (AGENTS.md): a change is not done until
   framework, both examples, scaffold templates, doctor, README,
   changelog, and tests agree.
6. **Adversarial review before each release.** A green checklist proves
   the code does what its own tests say — it cannot catch what the test
   author didn't imagine. Before a release is cut, the new work gets a
   fresh-eyes review that actively hunts for bugs (edge cases, wrong
   documents, authorization holes, doc drift), and significant findings
   are verified by reproduction before they are believed. Every bug
   this practice has caught so far lived exactly where no test looked,
   while the full checklist stayed green.

## Shipped

- **0.1.0** — contracts, routes, ASGI dispatch, error honesty, typed
  client, OpenAPI 3.1, lifespan resources, the CLI (`new`, `make`,
  `routes`, `openapi`, `doctor`, `dev`).
- **0.2.0** — `tenchi doctor` import-boundary checks, typed request
  headers, the hooks seam for authentication.
- **0.3.0** — taskboard stress app, request-scoped context (per-request
  transactions), client ergonomics, the providers decision.
- **0.4.0** — `tenchi.testing`, `tenchi.pagination`, `tenchi.health`,
  the policies convention, doctor's authorization consistency check,
  the middleware seam, request ids, OpenAPI security schemes.
- **0.5.0** — this roadmap and the events design note; the API snapshot
  guard; the transactional outbox demonstrated end to end in the
  taskboard; a framework-wide correctness pass from the first
  adversarial review (transaction honesty on 500s, custom-validator
  422s, OpenAPI collision refusal, doctor hardening).
- **0.6.0** — `tenchi.execution` (`execute`/`open_context`: the
  server's boundary guarantees at any entrypoint); HTTP boundary
  hardening (request body caps with a framework 413, RFC 9745/8594
  deprecation and sunset headers from contract metadata,
  `tenchi routes --json`).

## Ahead

Ordered by intent, not promise; each item still has to win its argument
when its turn comes.

- **TypeScript client recipe** — Tenchi's OpenAPI documents are
  standard 3.1, so best-in-class generators should consume them as-is;
  the work is a CI conformance test that generates and typechecks a
  client, plus a documented recipe. A Tenchi-owned generator only if
  the recipe hits a real gap.
- **Docs site** — the README is carrying a lot; a small mkdocs-material
  site with a tutorial, the design notes, and a reference belongs before
  any adoption push.
- **Idempotency keys** — a design note first: the outbox makes effects
  exactly-once outward; idempotency keys would make writes exactly-once
  inward, and the request-scoped transaction already makes
  check-and-record atomic. Decide on paper, prove in the taskboard.
- **Comparison document** — "Tenchi vs FastAPI / Django / Litestar",
  written from the lane statement: what you give up, what you get.
- **On demonstrated demand only** — `tenchi make adapter`, integration
  documentation (not packages) for common stacks.

## How to propose a feature

Ask, in order: (1) Is it a port and an adapter in application code? Then
it needs documentation, not framework. (2) Does it survive the
anti-roadmap? (3) Which two real uses earn it? (4) Which README section
teaches it? A proposal that answers all four is probably right; write
the design note first (`docs/`), decide, then build.
