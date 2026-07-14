# taskboard

Tenchi's stress-test application: two related features (projects and
tasks), bearer-token authentication with identity on the context,
ownership rules enforced in use cases, pagination, partial updates, and
SQLite adapters sharing one lifespan-managed connection. If a framework
capability regresses, something here should break.

```sh
uv sync
uv run pytest
uv run tenchi doctor
uv run tenchi dev
```

Demo tokens are wired in `app/server/asgi.py`: `alice-token` and
`bob-token`.

```sh
curl -X POST localhost:8000/projects \
  -H 'authorization: Bearer alice-token' \
  -H 'content-type: application/json' \
  -d '{"name": "Launch"}'
```
