# Expose todo listing as an application tool

Expose the existing `list_todos` use case as a Tenchi application tool.

Requirements:

- Name the tool `todos.list`.
- It takes no application input and returns `list[Todo]`.
- Describe it as listing the application's todos.
- Mark it read-only, idempotent, non-destructive, and closed-world.
- Bind the declaration directly to the existing `list_todos` use case; do not
  duplicate that behavior in a new function.
- Compose the feature tool group through `app.server.tools:tools`.
- Add direct manifest and runner tests.
- Review the tool compatibility change before updating `tools.json`.
- Do not add model, MCP transport, or authentication machinery for this task.
- Do not weaken `tenchi.toml`, remove existing behavior, or edit Tenchi itself.

Use the repository's `AGENTS.md` workflow and finish with a passing
`tenchi verify` receipt against the immutable starting commit.
