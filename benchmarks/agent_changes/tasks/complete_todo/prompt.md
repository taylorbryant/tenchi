# Complete a todo

Add `PATCH /todos/{todo_id}/complete` to this Tenchi application.

Requirements:

- Add a validated path-parameter model and a contract named
  `complete_todo_contract`.
- Return the completed `Todo` with `completed=true`.
- Persist completion through both `MemoryTodoRepository` and
  `SqliteTodoRepository` by extending the `TodoRepository` port with
  `complete(todo_id: str) -> Todo | None`.
- Raise the existing declared `TODO_NOT_FOUND` application error when the todo
  does not exist.
- Bind the contract to one plain async use case named `complete_todo`.
- Add meaningful direct use-case tests and HTTP integration coverage.
- Review the OpenAPI compatibility change before updating `openapi.json`.
- Do not weaken `tenchi.toml`, remove existing behavior, or edit Tenchi itself.

Use the repository's `AGENTS.md` workflow and finish with a passing
`tenchi verify` receipt against the immutable starting commit.
