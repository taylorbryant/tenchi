# Tenchi product direction

This is the agreed direction for developing Tenchi. It gives maintainers and
coding agents the context needed to make consistent decisions in a new
conversation. [AGENTS.md](AGENTS.md) contains the implementation rules and
verification requirements. This document guides prioritization; it does not
change public APIs or describe unimplemented proposals as available features.

## Purpose

Tenchi is a contract-first Python framework for backends that humans and
coding agents can understand, build, review, and verify.

The primary user is a developer building and maintaining a typed backend,
often with a coding agent. The application can be an ordinary inventory,
billing, or collaboration API with no model calls. Agent-native describes
the development experience: predictable code, explicit boundaries,
discoverable relationships, actionable diagnostics, and reviewable evidence.

The goal is to make a realistic application change easier to complete
correctly and review confidently. Human understanding and control remain
requirements even when an agent writes the code.

## How the original directions fit

| Direction | Role in Tenchi |
| --- | --- |
| A Python version of Beignet | Architectural heritage: contracts, use cases, ports, explicit composition, and tooling that reinforces the application model. Adopt concepts with Python-native ergonomics. |
| A Laravel-like Python framework | Developer experience: conventions, useful defaults, a working starter, and a coherent daily workflow. |
| A minimalist contract-first framework | Design constraint: a small application model, plain Python behavior, and understandable runtime machinery. |
| An AI/agent-native framework | Primary product direction: help humans and coding agents understand, implement, and verify backend changes. |

Feature or API parity with Beignet and Laravel is not a goal. Nor is the
smallest possible HTTP library. Broader framework scope must earn its cost
through the development workflow and actual application needs.

## Product boundaries

### Development tooling is central

Generated `AGENTS.md`, the canonical application structure, structured CLI
results, `map`, previewable generators, `doctor`, `check`, `verify`, and the
coding-agent MCP server support the primary purpose. Preserve a supported
default path for coding agents, including the generated MCP configuration.
The CLI must remain equally capable so filesystem-and-shell agents can use
the same operations without MCP or a particular model vendor.

An individual developer can work without a coding agent. Both use the same
ordinary Python source, tests, contracts, and validation operations. Keep
declarations and explicit wiring authoritative; avoid introducing a parallel
application specification that has to be synchronized with the code.

### Application capabilities are selective

Application tools, application MCP endpoints, evaluations, jobs, operational
tasks, and preflight checks are adopted as the application needs them.
Coding-agent MCP inspects and verifies development work; application MCP
exposes authenticated application behavior. Supporting the first does not
require an application to use the second.

Tenchi keeps infrastructure behind application-owned ports. The current
direction does not call for owning an ORM, queue service, scheduler, model
runtime, or agent orchestration system. Integrations and recipes can make
those choices easier while preserving explicit ownership and wiring.

### Complexity must pay for itself

Keep contracts, routes, plain async use cases, ports, and contexts as the
common model. Preserve composition-time checks, declared error semantics,
authorization boundaries, and validation before a transaction commits.

Prefer one predictable application layout. Evaluate complexity by how many
concepts and independent decisions a change requires, how often the same
decision must be restated, and how much code a maintainer must understand.
Extra files can be useful when they isolate responsibilities. Empty modules,
duplicate sources of truth, and layers that only forward configuration need
a concrete justification.

Do not collapse jobs, tasks, tools, and HTTP into a universal abstraction
solely because their implementations look similar. Their different boundary
guarantees matter. Likewise, changing all use-case signatures or supporting
multiple directory layouts requires evidence of recurring friction that
outweighs the additional concepts and migration cost.

## The workflow to optimize

1. Understand the affected feature and its dependencies from source and the map.
2. Specify the intended boundary and behavior, including errors and identity.
3. Preview generated structure and the required explicit wiring.
4. Implement the behavior and meaningful tests in ordinary Python.
5. Run deterministic checks and receive actionable failures.
6. Review the code and compatibility evidence against the intended change.

Every step should leave a human reviewer able to understand the result.
Automation should reduce repeated decisions and expose mistakes early.
Generators retain the repository's rule of creating files and reporting
wiring steps without silently rewriting existing modules.

Verification is a core capability with bounded claims. Structural completion,
successful test execution, boundary compatibility, and business correctness
require different kinds of evidence. A passing receipt cannot establish
assertion quality or full satisfaction of a product requirement.
Project-owned pytest evidence is cooperative; independent acceptance tests
belong outside an editable worktree when evaluating an untrusted code
producer. Additional verification machinery should address failures observed
in realistic development work.

## Current priorities

Favor consolidation and demonstrated workflow improvements over adding new
capabilities. These priorities guide future work; the 0.19 progress noted
below is implemented in the development tree and is not yet released:

- Reduce empty generated files and make application capabilities easier to
  adopt selectively, while retaining the default coding-agent tooling. The
  0.19 development cycle provides explicit per-feature `--with` selection and
  `make capability tools`; evaluate that adoption path before extending the
  generator to other capabilities.
- Keep the todos teaching path focused on the core model; demonstrate advanced
  behavior where taskboard and Fieldnotes give it a concrete application use.
- Separate large internal modules along existing responsibilities while
  preserving public APIs, diagnostics, and versioned result formats.
- Improve inspection, generation, diagnostics, and verification where actual
  feature work shows an unclear or repetitive step.

Use [the existing benchmark](benchmarks/agent_changes/README.md), example
applications, and human walkthroughs to evaluate proposals. Representative
changes include persisted contract evolution, authenticated owner scoping,
durable idempotency, transactional job handoff, and reuse through tools.

Assess acceptance-test success, regressions, human interventions, time and
cost under comparable conditions, and the effort needed to review the diff.
The benchmark already records some of this evidence; capture human review
and other unmeasured friction separately rather than claiming the harness
proves it. Model runs are deliberate experiments, never part of normal CI.

## Applying and maintaining this direction

For a substantial proposal, name the application task or recurring failure it
addresses, the decisions it removes, the new concepts and maintenance it adds,
and the evidence that would show an improvement. A bug fix does not need a
new strategy exercise. Avoid growing process around routine changes.

Treat this direction as the default when a task leaves a design choice open.
Record deliberate changes to the direction here and update the summary in
`AGENTS.md` in the same change. Update public guides and generated instructions
when their promises or workflows change; proposed work must remain clearly
distinguished from current behavior. Keep the reasoning in the repository so
future threads can recover it without this conversation.
