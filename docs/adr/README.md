# Architecture Decision Records

This directory records the decisions that shape Konstruktor: what it is, what it refuses to
pretend it does, and which contracts other people may depend on.

An ADR is written once, when the decision is made. It is not a blog post, not a meeting log, and
not a place to collect design ideas.

## When an ADR is required

Write one when the change matches any of these:

- the decision is expensive to reverse, because users, deployments, or stored artifacts would have
  to migrate;
- it changes a public contract: CLI surface, exit codes, artifact layout, configuration schema, or
  the shape of the agent event stream we parse;
- it concerns security, isolation, secrets, or the limits of what this tool can guarantee;
- it picks between alternatives that a competent newcomer would reasonably challenge;
- it changes a dependency, a runtime, or the execution model.

An ADR is **not** required for:

- bug fixes and behavioral corrections;
- formatting, renaming, or refactoring covered by equivalent tests;
- routine dependency bumps inside an already-declared range;
- new tests, CI plumbing, or documentation that does not encode a decision.

When in doubt: write the ADR. The cost of an unnecessary ADR is small; the cost of an unrecorded
decision is paid by whoever has to reconstruct it later.

## Writing rules

1. **One decision per file.** If the text contains "and", it is probably two ADRs.
2. **Evidence over assertion.** Every non-obvious claim about current behavior cites `file:line`.
   A claim about intent cites the commit that shows it. An ADR with no anchors is an opinion.
3. **Do not invent rationale.** If the reason a decision was made was never recorded, say so in
   *Rationale provenance* and label it as reconstructed. An ADR that guesses a rationale and states
   it as fact is worse than no ADR, because it will be trusted later.
4. **Record the decision, not the deliberation.** Alternatives are listed with one line on why each
   was not chosen. Nobody needs the transcript.
5. **Consequences include the bad ones.** A decision with only positive consequences has not been
   examined.

## Naming and numbering

- Files are `docs/adr/NNNN-kebab-title.md`, four digits, lowercase kebab-case, starting at `0001`.
- Numbers are assigned in recording order and are never reused, never renumbered, and never
  reordered. A deleted decision keeps its number reserved.
- An ADR that is later reversed is **superseded**, not edited. The old file stays as it was.

## Statuses

| Status | Meaning |
|---|---|
| `Proposed` | Written, not yet agreed. Merging it commits to the decision. |
| `Accepted` | In force. |
| `Superseded by ADR-NNNN` | Replaced by a later decision. The link is mandatory. |
| `Deprecated` | No longer relevant, not replaced by anything. |
| `Rejected` | Recorded so the option is not re-proposed from scratch. |

## Workflow

1. Open the ADR as `Proposed` before or in the same pull request as the change it describes.
2. Discussion happens in the pull request, not by editing the file afterwards.
3. On agreement, flip the status to `Accepted` in the same pull request as the change.
4. If implementation reveals the decision was wrong, write a new ADR that supersedes the old one.
   Never rewrite history to hide it.

## Template

````markdown
# ADR-NNNN: <Title>

- **Status:** Proposed | Accepted | Superseded by ADR-NNNN | Deprecated | Rejected
- **Date:** YYYY-MM-DD
- **Deciders:** <who agreed>
- **Evidence:** <commit sha> (YYYY-MM-DD); `path/file.py:12-34`

## Context

What forced a decision. Constraints, existing contracts, what was already true.

## Decision

What was decided, in the present tense, specific enough to be checked against the code.

## Alternatives considered

Each with one line on why it was not chosen.

## Consequences

What becomes true because of this, including what gets harder.

## Rationale provenance

How the rationale was established: recorded at the time, or reconstructed, and from what.
````

## Index

| ADR | Title | Status | Date |
|---|---|---|---|
| [0001](0001-wrap-openhands-cli-as-subprocess.md) | Wrap the OpenHands CLI as a subprocess | Accepted | 2026-10-17 |
| [0002](0002-separate-run-and-plan-commands.md) | Separate `run` and `plan` top-level commands | Accepted | 2026-10-17 |
| [0003](0003-spec-driven-plan-pipeline.md) | Spec-driven pipeline, one agent session per stage | Accepted | 2026-10-17 |
| [0004](0004-compliance-matrix-verification-contract.md) | Verification is a machine-parsed compliance matrix | Accepted | 2026-10-17 |
| [0005](0005-artifacts-are-the-source-of-truth.md) | Written artifacts are the source of truth | Accepted | 2026-10-17 |
| [0006](0006-fail-closed-runtime-security.md) | Fail closed on security controls that cannot be enforced | Accepted | 2026-10-17 |
| [0007](0007-exit-code-contract.md) | Exit codes are a public contract | Accepted | 2026-10-17 |
| [0008](0008-configuration-layering.md) | Configuration layering with an optional user file | Accepted | 2026-10-17 |
| [0009](0009-per-run-diagnostics-layout.md) | Per-run diagnostics directory with bounded retention | Accepted | 2026-10-17 |
| [0010](0010-timeout-and-termination-model.md) | Timeout, step-idle and step-count limits | Accepted | 2026-10-17 |

## Validation

`tests/test_adr_docs.py` enforces the structure of this directory: file naming, numbering,
required sections, the status vocabulary, that every ADR appears in the index above with a matching
status, and that the root copy of the default configuration still agrees with the packaged one. It
runs as part of `pytest -v`.
