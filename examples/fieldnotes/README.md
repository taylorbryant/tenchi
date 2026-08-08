# Fieldnotes

Fieldnotes is a cited personal-research backend built with Tenchi. Save text
or URL-attributed material, index it in a background worker, search your own
passages, and ask questions that return exact citations.

The default answer provider is deterministic and requires no credentials. It
returns the highest-ranked passage verbatim, which keeps local development and
the evaluation gate reproducible. Replace that adapter with a model-backed
implementation of `AnswerGenerator` when you want generated synthesis.

## Run the API

```shell
uv sync
uv run tenchi check
uv run tenchi dev
```

The development server uses `fieldnotes.db` and recognizes `alice-token` and
`bob-token`. In another terminal, run the indexing worker:

```shell
uv run python -m app.server.worker
```

Save material:

```shell
curl http://127.0.0.1:8000/sources \
  -H 'Authorization: Bearer alice-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "MCP security",
    "url": "https://example.com/mcp",
    "content": "Explicit approval protects destructive MCP tools."
  }'
```

After the worker indexes it, ask a question with citations:

```shell
curl http://127.0.0.1:8000/answers \
  -H 'Authorization: Bearer alice-token' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What protects destructive tools?"}'
```

The result includes `has_citations: true` and a citation naming the exact saved
source and passage. This flag reports citation presence, not a semantic
grounding judgment. Sources and search results are isolated by authenticated
owner.

Run the bearer-authenticated application MCP endpoint separately:

```shell
uv run uvicorn app.server.mcp:app --port 8001
```

Connect to `http://127.0.0.1:8001/mcp` with the same bearer token. The default
server exposes read tools and returns `approval_required` for `sources.save`;
deployments supply their own identity directory, approval callback, and
transport-security settings.

## Run operational and AI gates

```shell
uv run tenchi task run knowledge.reindex_sources \
  --input '{"dry_run":true}'
uv run tenchi eval run
uv run tenchi preflight
```

See the [Fieldnotes guide](https://tenchi.io/fieldnotes) for provider deadlines,
MCP approval behavior, and the model-evaluation migration.
