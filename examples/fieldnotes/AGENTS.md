# Fieldnotes agent guide

Fieldnotes is Tenchi's reference backend for cited personal research. Read
the repository root `AGENTS.md` before editing; all framework architecture and
verification rules apply here.

## Local application rules

- `knowledge` owns sources, indexing messages, passage retrieval, answers,
  tools, and evaluations.
- HTTP and application tools bind the same use cases. Do not duplicate their
  behavior in transport adapters.
- Every owner-facing operation derives `OwnerScope` from authenticated context.
  Worker and task use cases carry the explicit `# doctor: public` pragma.
- Saving a source and its indexing message must remain one SQLite transaction.
- Model providers implement `AnswerGenerator` in `app/infra/`. Prompts, model
  output, and credentials never belong in Tenchi result models or logs.
- A generated answer may cite only passage ids supplied to the provider. The
  use case validates that invariant before returning or committing.
- `has_citations` reports citation presence only; do not turn it back into a
  semantic grounding claim without a runtime verifier.
- Provider calls stay bounded in the shared use case so HTTP, tools, and MCP
  have the same application deadline and preserve external cancellation.
- Application MCP authenticates each request from transport headers. A fixed
  principal is only valid for an explicitly trusted, single-user process.
- The default deterministic provider must keep CI and local evaluation runs
  credential-free.
- When a model replaces it, classify the answer evaluation as `model`, report
  token and cost usage, set budgets, and review the policy snapshot change.
- `tenchi.toml` requires every completion-receipt stage. Fix failed evidence;
  do not weaken the policy to make a change pass.

## Validation loop

Run from `examples/fieldnotes`:

```shell
uv run tenchi map --feature knowledge --json
uv run tenchi check
uv run tenchi eval run
uv run tenchi verify --base-ref <historical-ref> --json
```

Review OpenAPI, tool, and evaluation-policy diffs before writing changed
snapshots.
