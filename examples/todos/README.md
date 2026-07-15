# todos

Tenchi's teaching example: one feature, every core concept once —
contracts, schemas, a `typing.Protocol` port, plain use cases, policies
via an optional API-key hook, memory and SQLite adapters, explicit
wiring, OpenAPI, and a health route. It matches the structure `tenchi
new` scaffolds, so it doubles as the reference for what generated apps
grow into.

Run it from the repository root (the root environment includes this
example's dependencies):

```sh
uv sync
uv run pytest examples/todos       # use-case + HTTP tests
uv run uvicorn app.server.asgi:app --reload --app-dir examples/todos
```

```sh
curl -X POST localhost:8000/todos \
  -H 'content-type: application/json' \
  -d '{"title": "Buy milk"}'
```

Set `TODOS_API_KEY` to require an `x-api-key` header on every route
except the OpenAPI document and the health check.
