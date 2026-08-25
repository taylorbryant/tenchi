# Add an authenticated, owner-scoped todo read

The starting application already authenticates the todo API and scopes create
and list behavior to the current user. The demo credentials are `alice-token`
for user `alice` and `bob-token` for user `bob`.

Add `GET /todos/{todo_id}`. It returns the authenticated user's todo and raises
the declared `TODO_NOT_FOUND` error for both an unknown id and an id owned by
someone else. Extend the repository port and both adapters with explicit
keyword arguments named `owner` and `todo_id`. Derive the `OwnerScope` from
`context.user` inside the new use case; request data must never choose the
owner. Do not expose `owner_id` in the HTTP model.

Bind the operation, declare its not-found error, add focused use-case and HTTP
tests, and update the OpenAPI snapshot. Existing authenticated create/list
behavior, public service routes, bearer security, and legacy unowned-row
handling must remain intact. Inspect compatibility before accepting the
additive snapshot change.

Use the repository's `AGENTS.md` workflow and finish with a passing
`uv run tenchi verify --base-ref HEAD`.
