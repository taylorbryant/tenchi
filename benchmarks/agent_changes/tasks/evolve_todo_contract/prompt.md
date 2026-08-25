# Add optional todo descriptions

Users need to attach a short description to a todo. Evolve the existing API so
`POST /todos` accepts an optional `description` string with a maximum length of
500 characters and every returned `Todo` includes `description`, defaulting to
`null` when it was omitted.

Carry the field through the request and response schemas, use case, repository
port, memory adapter, SQLite adapter, contract examples, tests, and OpenAPI
snapshot. The database migration must preserve rows created by the previous
schema, and clients that still send only `title` must continue to work.

Do not add a second endpoint or bypass the repository port. Add focused tests
for the behavior you introduce. Inspect compatibility before accepting the
updated OpenAPI snapshot.

Use the repository's `AGENTS.md` workflow and finish with a passing
`uv run tenchi verify --base-ref HEAD`.
