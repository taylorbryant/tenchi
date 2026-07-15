# Design note: providers in Tenchi

Status: decided (2026-07). Tenchi does **not** ship a tier of provider
packages. Ports + adapters + scoped resources are the whole story.

## The question

Batteries-included frameworks often grow a tier of
`provider-<capability>-<implementation>` packages (database drivers, mail
senders, cache backends, ...) that adapt external systems to framework
primitives — Laravel's first-party ecosystem is the fullest expression,
and several TypeScript frameworks follow the same pattern. Should Tenchi
grow one?

## The decision

No — not unless a concrete integration proves it necessary. The evidence
from building two real apps (`examples/todos`, `examples/taskboard`):

1. **The pattern already works with zero framework support.** Both apps
   wired real SQLite adapters using nothing but `typing.Protocol` ports,
   plain adapter classes in `infra/`, `open_*` async-context-manager
   factories, and `create_app(lifespan=...)`. No framework primitive was
   missing; nothing about the wiring felt like boilerplate a package
   could remove.
2. **Provider tiers mostly solve other ecosystems' problems.** Where
   vendor SDKs need adapting into framework-owned lifecycles and
   instrumentation, a package tier earns its keep. Python libraries
   (aiosqlite, SQLAlchemy, redis-py, httpx) already expose
   context-managed lifecycles and well-typed clients; an adapter class
   over a port is a page of obvious code.
3. **A package tier has real costs**: release trains, version matrices,
   and — worst — the gravitational pull to design app conventions around
   what providers expose rather than what use cases need.

## What we do instead

- **Document the adapter pattern as the way** (README, examples, this
  note): a port in `features/<feature>/ports.py`, an adapter in `infra/`,
  an `open_*` factory owning the resource lifecycle, wiring in
  `infra/port_wiring.py`, composition in `server/asgi.py`.
- **Close the one real gap: request-scoped resources.** Process-scoped
  resources belong to the lifespan; but a per-request unit of work — a
  database transaction that commits on success and rolls back on error —
  had no home. `create_app` therefore accepts a context factory that is an
  async context manager: entered at request start, exited at request end,
  with the exception (if any) flowing through `__aexit__` so `async with
  connection:`-style transaction semantics compose naturally.
- **Possible future sugar, only on demonstrated demand:** a
  `tenchi make adapter <feature> <name>` generator, and integration
  *documentation* (not packages) for common stacks (SQLAlchemy, redis).

## Revisit when

- An integration genuinely cannot be expressed as port + adapter +
  scoped resource (candidates: background jobs, event buses — which are
  new capabilities, not providers for existing ones).
- Multiple apps demonstrably copy-paste the same non-trivial adapter, at
  which point a shared package earns its maintenance cost.
