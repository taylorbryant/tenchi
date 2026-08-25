# Repair the missing todo tool

The todos feature already declares and binds a read-only `todos.list` tool in
`app/features/todos/tools.py`, but clients cannot discover or invoke it. Find
the composition problem using Tenchi's inspection and verification commands,
then repair the wiring without duplicating the tool declaration or use case.

Add a focused application test that invokes the composed tool against persisted
todo data. Inspect the tool compatibility change before accepting the updated
`tools.json` snapshot. Leave the application map free of diagnostics and
unresolved relationships.

Use the repository's `AGENTS.md` workflow and finish with a passing
`uv run tenchi verify --base-ref HEAD`.
