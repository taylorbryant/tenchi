# Tenchi coding-agent benchmark

This benchmark measures whether a coding agent can turn a user-style backend
request into a production-shaped Tenchi change without human rescue. It is a
repository development tool, not part of the published framework or an agent
leaderboard.

Every run starts from a newly rendered Tenchi scaffold in a disposable Git
repository. The agent receives only that application, its generated
`AGENTS.md`, and `TASK.md`. After the agent exits, the harness installs
repository-owned hidden acceptance tests and requires both those tests and
`tenchi verify` against the immutable starting commit to pass.

The task digest covers the task corpus, evaluator harness, and current Tenchi
framework source. A framework or evaluator change therefore produces a new
benchmark identity instead of silently mixing unlike runs.

The initial corpus covers:

- a persisted state-changing HTTP operation;
- a read operation with a declared, machine-readable application error; and
- a read-only application tool bound to existing behavior.

## Keep the agent isolated

Hidden tests are absent from the prepared workspace until evaluation. Configure
the agent's filesystem sandbox so it can read and write only that workspace.
Running an agent with access to this Tenchi checkout would let it inspect the
hidden evaluator and invalidates the result.

The generated workspace installs the current Tenchi checkout through a local
file dependency. Do not count dependency synchronization as agent time; the
harness completes `uv sync` before creating the baseline commit.

## List tasks

```sh
uv run python -m benchmarks.agent_changes list
uv run python -m benchmarks.agent_changes list --json
```

## Run an agent command

The integrated runner sends the task prompt to the agent command on standard
input and sets `TENCHI_BENCHMARK_TASK`, `TENCHI_BENCHMARK_PROMPT_PATH`, and
`TENCHI_BENCHMARK_WORKSPACE`. The command runs with the prepared application as
its working directory:

```sh
uv run python -m benchmarks.agent_changes run complete_todo \
  --output /tmp/tenchi-complete-todo-run-1.json \
  --agent-label agent-name \
  --interface mcp \
  --attempt 1 \
  --interventions 0 \
  /tmp/tenchi-complete-todo-run-1 \
  -- agent-command-that-reads-stdin
```

Options may appear before or after the task and workspace. The `--` separator
is required because everything after it belongs to the agent command.

For example, the installed Codex CLI can run a CLI-only baseline with:

```sh
uv run python -m benchmarks.agent_changes run \
  --output /tmp/tenchi-get-todo-run-1.json \
  --agent-label codex-default \
  --interface cli \
  --attempt 1 \
  --interventions 0 \
  get_todo \
  /tmp/tenchi-get-todo-run-1 \
  -- \
  codex exec \
    --ephemeral \
    --ignore-user-config \
    --approve-for-me \
    -
```

`--approve-for-me` already selects Codex's workspace-write sandbox. Do not
combine it with Codex's separate `--sandbox` option.

The harness deliberately does not prescribe a model vendor or agent CLI. Use a
small adapter when the selected agent cannot receive its prompt on standard
input.

Agent standard output and error remain attached to the invoking terminal and
are never copied into the result. The versioned result contains only task and
agent labels, a content digest identifying the exact task corpus, timing and
intervention counts, changed paths, stable evaluator statuses, and exit codes.
It never contains prompts, application values, hidden-test failures, or agent
logs.

The complete list, state, result, and summary shapes are retained in
`protocol-v1.json`. Bump the benchmark protocol version and retain the previous
snapshot before making a breaking or ambiguous machine-readable change.

Evaluation never trusts commands from the agent-writable `.venv`. It runs
pytest and Tenchi through the benchmark process's interpreter, with a retained
pytest configuration for hidden tests. `TASK.md`, `pyproject.toml`,
`tenchi.toml`, and `uv.lock` are protected benchmark inputs: changing any of
them fails task integrity. The initial tasks require no dependency changes.

## Manage an external run

Use separate preparation and evaluation when another system owns the agent
process. Keep the state file outside the agent's workspace and do not grant the
agent permission to read or change it:

```sh
uv run python -m benchmarks.agent_changes prepare get_todo \
  /tmp/tenchi-get-todo-run-1 \
  --state /tmp/tenchi-get-todo-run-1.state.json

# Run the isolated agent in /tmp/tenchi-get-todo-run-1.

uv run python -m benchmarks.agent_changes evaluate \
  --state /tmp/tenchi-get-todo-run-1.state.json \
  --output /tmp/tenchi-get-todo-run-1.json \
  --agent-label agent-name \
  --interface cli \
  --agent-status passed \
  --agent-exit-code 0 \
  --agent-duration 420 \
  --attempt 1 \
  --interventions 0
```

An externally managed run is scored from the supplied metadata and evaluator
evidence. Use `--agent-status external` only when the surrounding system cannot
report the process outcome; such a run can pass evaluation but is not counted
as first-pass evidence.

Evaluation runs every changed `test_*.py` file explicitly before it injects the
hidden acceptance tests. It leaves those hidden tests in the disposable
workspace for debugging, so never resume the agent or reuse that workspace for
another measured attempt after evaluation.

## Aggregate repetitions

Run every task at least three times before drawing conclusions. Aggregate the
versioned results without exposing their application workspaces:

```sh
uv run python -m benchmarks.agent_changes report \
  /tmp/tenchi-complete-todo-run-1.json \
  /tmp/tenchi-complete-todo-run-2.json \
  /tmp/tenchi-complete-todo-run-3.json
```

`ok` requires an acceptable agent process outcome, an unchanged task prompt, at
least one passing agent-authored changed test file, passing hidden tests, and a
valid passing `tenchi verify` JSON receipt. The task digest prevents results
from different revisions of the same named task from being aggregated together.
`first_pass` additionally requires attempt 1 and zero human interventions.
Pass rates diagnose workflow friction; they are not broad claims about a model's
coding ability.

Normal CI tests the task loader, workspace isolation, process lifecycle,
payload schemas, hidden-test syntax, and report aggregation. It does not invoke
a model. Real runs belong in a deliberate manual or scheduled workflow with an
explicit model and cost budget.

The harness executes agent-written application code during evaluation. Treat
benchmark workspaces as untrusted and run manual or scheduled evaluations in a
disposable operating-system sandbox with network and credential access denied.
