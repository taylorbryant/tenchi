# Recipe: read replicas and read/write splitting

Status: documented pattern (2026-07), demonstrated in
`examples/taskboard`. No framework support required — and this note
records why the tempting framework-shaped answers are worse.

## The question

The application should serve certain reads from a read-only replica and
all writes (plus consistency-critical reads) from the primary. Where
does the routing decision live in a Tenchi app?

## The principle

**"This read can tolerate staleness" is a semantic contract, not an
infrastructure detail.** A replica read is not just a read from
somewhere else — it may be seconds behind. In Tenchi, semantic
contracts live in ports, so the decision goes in the type system where
every reader of a use case can see it:

```python
# features/tasks/ports.py
class TaskRepository(Protocol):   # writes + strong reads: primary
    async def create(self, *, project_id: str, title: str) -> Task: ...
    async def get(self, task_id: str) -> Task | None: ...
    async def save(self, task: Task) -> Task: ...

class TaskSearch(Protocol):       # staleness-tolerant: may be a replica
    async def search(self, *, viewer: OwnerScope, ...) -> tuple[list[Task], int]: ...
```

A use case that lists takes `TaskSearch`; a use case that writes takes
`TaskRepository`. Nobody routes at runtime — the signature already
decided.

## The wiring

The composition root opens both connections per request. `AsyncExitStack`
keeps multi-resource acquisition flat and unwinds it in reverse on exit
or error (`examples/taskboard/app/infra/port_wiring.py` is the working
version):

```python
@asynccontextmanager
async def open_request_ports(state: Pools) -> AsyncGenerator[AppPorts]:
    async with AsyncExitStack() as stack:
        primary = await stack.enter_async_context(state.primary.connection())
        await stack.enter_async_context(primary.transaction())
        reader = await stack.enter_async_context(state.replica.connection())
        yield AppPorts(
            tasks=SqlTaskRepository(primary),
            task_search=SqlTaskSearch(reader),
        )
```

The unit-of-work semantics stay clean by construction: the request
transaction wraps **only the primary** — commit and rollback apply to
writes — while the read connection is autocommit and participates in
nothing, exactly as a replica would not.

In the taskboard the "replica" is a second SQLite connection locked
into `PRAGMA query_only = ON`; against Postgres it would be a
connection from a replica pool. The wiring shape — and every property
below — is identical.

## The properties you get, and their tests

- **Read-your-writes is structural.** A writing use case holds the
  repository port, so its own mid-flow reads hit the primary inside its
  transaction. It cannot accidentally read the replica, because the
  stale-tolerant port isn't in its hand.
  (`test_read_connection_sees_only_committed_data` also pins the
  inverse: the read side never sees an open transaction.)
- **The read side cannot be written.** A wiring mistake that hands the
  read connection to a write path fails loudly, as a real replica
  would. (`test_read_connection_rejects_writes`.)
- **The worker keeps its one-connection unit of work.** The port
  declares staleness *tolerance*, not a replica *requirement* — the
  worker binds `TaskSearch` to its primary connection, because a job
  runs inside one write transaction.

## Cross-request staleness

Within a request you're covered; across requests (client writes, then
immediately reads) replica lag is physics. The standard mitigation —
brief read-from-primary stickiness after a write — has a native Tenchi
seam: hooks may return a replacement context, so a hook that sees a
"recently wrote" signal (cookie, JWT claim) can swap the reader ports
for primary-backed ones:

```python
def prefer_primary_after_write(info: RequestInfo, context: AppContext):
    if info.headers.get("x-recent-write"):
        return replace(context, task_search=primary_backed_search(context))
    return None
```

Five visible lines at the boundary, not a framework subsystem.

## What not to do

- **A silently routing adapter** — one port whose implementation sends
  reads to the replica and writes to the primary. A use case that
  saves and then gets reads the pre-write state with nothing in the
  code to warn anyone. If a team insists on one port, the defensible
  variant is *pin-after-write* (route reads to the replica until the
  first write on this context, then pin to primary) — a page of infra
  code, but prefer saying it in the signature.
- **Routing by HTTP method.** "GET → replica" is the wrong altitude:
  plenty of mutating handlers read first, and read-only handlers
  sometimes need strong reads. Tenchi's context factory deliberately
  cannot see the request, so this is inexpressible — declare the need
  per port instead.

## Revisit when

- An app needs many reader/writer port pairs and the split becomes
  boilerplate — that would be evidence for a convention (naming,
  scaffold support), still not for runtime routing.
- Someone needs per-request routing that ports genuinely cannot
  express; the compatible extension would be a richer context-factory
  signature, which waits for two real demands per the complexity
  budget.
