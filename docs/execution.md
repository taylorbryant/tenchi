# Design note: the shared execution model

Status: decided (2026-07). `tenchi.execution` provides `execute()` — one
blessed way to invoke a use case from any entrypoint with the boundary
guarantees the HTTP server already provides — and `open_context()`, the
context-scoping semantics shared verbatim with `create_app`. Nothing
else: no hooks, no instrumentation, no correlation ids, no output
validation, until real uses demand them.

## The question

HTTP is one caller of a use case, not its owner. Workers, scripts,
schedulers, and tests call the same functions — and when the taskboard's
outbox worker was built, it had to hand-roll what the server already
does: validate a raw payload against a declared type, and run the call
inside a unit of work whose exit sees success or failure. A second
non-HTTP entrypoint would copy that again. Beignet answers this with a
framework-wide execution layer; what is the Tenchi-sized version?

## Forces

- **Two real uses existed before the abstraction.** HTTP dispatch and
  the outbox worker independently needed identical semantics — this is
  the complexity budget's bar for adding framework surface, met the
  honest way around (duplication first, abstraction second).
- **The use case must stay a plain function.** Anything that wraps,
  decorates, or subclasses breaks the promise that a use case is
  testable by calling it.
- **Outside HTTP there is no wire metadata.** A contract exists because
  HTTP needs method, path, status, and media types. A queue payload
  needs none of that — the only fact required is the input type, and
  the use case's own ``request`` annotation already states it. A
  separate declaration object would duplicate the signature and could
  drift from it.

## The decision

`execute(use_case, *, context, request=... | request_json=...)`:

- Input is validated against the ``request`` parameter's annotation —
  Python data via ``validate_python``, raw JSON via ``validate_json`` —
  before the context opens, so invalid input never starts a unit of
  work. Inputs the use case has no parameter for are rejected, not
  dropped (the same honesty rule as the typed client).
- ``context`` accepts exactly what ``create_app(context_factory=...)``
  accepts: a value, a factory, an async factory, or a factory returning
  an async context manager. The scoping lives in ``open_context()``,
  which the server now uses too — commit-on-success / rollback-on-error
  is defined and tested once, not per entrypoint.
- Errors propagate. How a failure is surfaced — dead-letter, exit code,
  HTTP status — belongs to the entrypoint, so `execute` maps nothing.

The taskboard worker is the proof: its job registry dropped from
``{name: (payload_type, use_case)}`` to ``{name: use_case}``, and its
hand-rolled validation became one `execute` call.

## What was deliberately left out

Each of these appeared in the "Python Beignet" proposal; each is
deferred for the same reason — no second real use today:

- **Hooks and policy gates.** The worker trusts its queue; scripts trust
  their operator. The first real case of "every non-HTTP execution must
  pass through X" can add a ``hooks=`` parameter compatibly.
- **Instrumentation and timing.** Entrypoints log today. A timing seam
  that two real consumers need can be added without breaking anyone.
- **Correlation ids.** HTTP has request ids; the worker logs job row
  ids. A cross-entrypoint trace id becomes worth standardizing when a
  durable message actually propagates one.
- **Output validation.** HTTP validates responses because they cross a
  wire in a declared format. A worker's return value crosses nothing.
- **Multi-input use cases** (``params``/``query``/``headers``). Those
  shapes exist for HTTP; other entrypoints call them with typed values
  directly.

## Revisit when

- A second entrypoint kind (a scheduler, a CLI runner) needs semantics
  `execute` lacks, rather than semantics it already has.
- The parked `job()` declaration from `docs/events.md` becomes live —
  it would compose with `execute`, not replace it.
