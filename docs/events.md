# Design note: events and background work

Status: decided (2026-07). Tenchi ships no event bus and no job runtime.
Direct side effects are ports called by use cases; deferred work is
queued through a port using the transactional-outbox pattern; workers
are ordinary entrypoints composing the same use cases. The framework
adds nothing until two real uses demand it.

Demonstrated end to end in `examples/taskboard`: `add_project_member`
enqueues a `member_added` job through the `Outbox` port on the request's
transaction, and `app/server/worker.py` validates payloads at the
boundary and delivers notifications through an ordinary use case. The
whole demonstration was a page of obvious code per piece — evidence the
pattern needs no framework support yet.

## The question

"After the todo is created, send an email." Every framework answers
this somewhere: Django with signals, Rails with callbacks, FastAPI with
`BackgroundTasks`, most large systems with Celery or an equivalent.
Where does it live in a Tenchi app — and does Tenchi need machinery
for it?

## Forces

- **Honesty.** Tenchi's whole bet is that control flow is visible: a
  use case's effects are its port calls. An event emitter reintroduces
  invisible control flow — "who subscribes to this?" is the same
  question as "which plugin handles this?", and the anti-roadmap
  already rejects plugin systems.
- **Transactions.** Requests may run inside a per-request unit of work
  (the async-context-manager context factory). Deferred work enqueued
  during a request must not survive a rollback (ghost jobs for state
  that never existed) and must not be lost after a commit (paid orders
  that never ship). Any answer that dodges this — including
  fire-after-response helpers like `BackgroundTasks` — is wrong before
  it starts.
- **Boundary discipline.** A worker consuming a queue is a boundary,
  exactly like an HTTP request: payloads arrive as untyped bytes and
  must be validated before a use case sees them. The rules should be
  the same rules, not a parallel weaker set.

## The decision

Three rules, in order of preference:

**1. A direct effect is a port.** If the effect is part of the use
case's contract — creating an order sends a receipt — declare a port
and call it:

```python
# features/orders/ports.py
class ReceiptSender(Protocol):
    async def send(self, *, order: Order) -> None: ...
```

The effect is visible in the use case, fakeable in tests, swappable in
`infra/`. No events, no indirection. Most "we need events" cases are
this case.

**2. A deferred effect is enqueued through a port — transactionally.**
When the effect must not block or fail the request (send the email
later, retry on failure), the owning feature declares an outbox port:

```python
# features/orders/ports.py
class Outbox(Protocol):
    async def enqueue(self, *, job: str, payload: Mapping[str, Any]) -> None: ...
```

The use case calls `await context.outbox.enqueue(job="send_receipt",
payload={"order_id": order.id})` — still a visible port call. The
production adapter writes a row to an outbox table **on the same
connection as the request's transaction**, which is exactly what the
request-scoped context factory already provides. Commit persists the
state change and the job atomically; rollback discards both. The test
adapter is a list.

**3. A worker is an entrypoint, not a framework feature.** A worker
process looks like `server/asgi.py`'s sibling: it opens the same
lifespan resources, builds the same context, and calls ordinary use
cases. It polls the outbox (or consumes whatever queue the adapter
feeds), validates each payload at the boundary with a `TypeAdapter` —
undeclared or malformed payloads are dead-lettered, mirroring the HTTP
honesty rule — and dispatches on the job name. Delivery semantics,
retries, and backoff belong to the queue technology behind the adapter,
not to Tenchi.

## What we will not build

- **A global event emitter or signals.** Hidden subscribers are hidden
  control flow; `tenchi doctor` could never again answer "what does
  this use case do?" by reading it.
- **Decorator-registered handlers** (`@on("order.created")`). Same
  objection, plus import-order magic.
- **A fire-after-response helper.** It loses work on any crash and
  silently dodges the transaction question; the outbox answers it.
- **A worker runtime.** Supervision, concurrency, and scheduling are
  solved by the process manager and queue system you already run.

## What the framework might add later

Nothing today. Two candidates, each priced under the complexity budget
(two real uses, one README section) and to be reconsidered only after
the taskboard grows a real deferred effect:

- **A typed job declaration** — a `job()` analog of `contract()`
  binding a job name to a payload type, so enqueuers and workers share
  one declaration, payload validation is automatic on both sides, and
  `doctor` can flag enqueued-but-never-handled names.
- **A testing helper** to drain an in-memory outbox and run the
  matching use cases inline, so integration tests can assert "the
  receipt was sent" without a worker process.

## Revisit when

- The taskboard (or a real app) implements its first deferred effect
  and the pattern above produces genuine boilerplate rather than a page
  of obvious code.
- Someone needs fan-out (one fact, several consumers) — the outbox row
  simply gains several job entries, but if that recurs, the typed job
  declaration gets more attractive.
