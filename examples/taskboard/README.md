# taskboard

Tenchi's stress-test application: two related features (projects and
tasks), bearer-token authentication with identity on the context,
ownership and membership rules enforced in use cases, pagination,
partial updates, SQLite adapters on a per-request connection and
transaction, a transactional outbox with a worker entrypoint
([`docs/events.md`](../../docs/events.md)), and read/write splitting —
staleness-tolerant listing runs on a read-only second connection through
its own port ([`docs/read-replicas.md`](../../docs/read-replicas.md)).
If a framework capability regresses, something here should break.

```sh
uv sync
uv run pytest
uv run tenchi doctor
uv run tenchi dev
```

Adding a project member enqueues a `member_added` notification job in
the same transaction as the membership change. Jobs are delivered by
the worker — run it alongside the server:

```sh
uv run python -m app.server.worker
```

Demo tokens are wired in `app/server/asgi.py`: `alice-token` and
`bob-token`.

```sh
curl -X POST localhost:8000/projects \
  -H 'authorization: Bearer alice-token' \
  -H 'content-type: application/json' \
  -d '{"name": "Launch"}'
```
