# Fetch one todo

Add `GET /todos/{todo_id}` to this Tenchi application.

Requirements:

- Use a validated path-parameter model.
- Return the matching `Todo` from both memory and SQLite repository adapters.
- Return the existing declared `TODO_NOT_FOUND` application error when no todo
  exists. The error must remain machine-readable in OpenAPI and at runtime.
- Keep storage lookup behind the feature-owned `TodoRepository` port.
- Bind one plain async use case and test it directly without HTTP.
- Add HTTP integration coverage for both the successful and missing cases.
- Review the OpenAPI compatibility change before updating `openapi.json`.
- Do not weaken `tenchi.toml`, remove existing behavior, or edit Tenchi itself.

Use the repository's `AGENTS.md` workflow and finish with a passing
`tenchi verify` receipt against the immutable starting commit.
