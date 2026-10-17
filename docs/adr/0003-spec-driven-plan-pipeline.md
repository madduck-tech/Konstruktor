# ADR-0003: Spec-driven pipeline, one agent session per stage

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/planner.py:698-963`,
  `src/konstruktor/planner.py:679-685`, `src/konstruktor/planner.py:1049-1077`,
  `src/konstruktor/planner.py:677`, `src/konstruktor/cli.py:186-196`

## Context

An autonomous agent that both decides what "done" means and judges its own work has no way to be
held to anything: the same context that skipped a requirement is the one that reports success. Long
sessions also accumulate drift, and a single session leaves behind nothing a human can read once it
finishes.

## Decision

`konstruktor plan` runs a fixed sequence of stages:

```text
Intent -> Spec -> Review -> Plan -> Implement -> Verify -> Fix -> Report
```

Two properties make this work:

1. **One OpenHands session per stage.** Every stage calls `KonstruktorRunner.run` separately
   (`planner.py:1049-1077`). No context is carried between stages. Stages communicate only through
   files.
2. **Artifacts live in the target project.** Under `<working-directory>/docs/specs/<task-slug>/`:
   `intent.md`, `spec.md`, `plan.md`, `compliance.md`, `report.md` (`planner.py:679-685`). The slug
   is derived from the task text (`planner.py:399-403`).

The stage order is fixed. The only optional stage is the spec review, controlled by
`plan.spec_lint_enabled` (`planner.py:741`). The pipeline timeout is set once at entry
(`planner.py:677`) and each stage is given the remaining budget as its own timeout
(`planner.py:1065-1073`), so the limit applies to the whole pipeline rather than to each stage
independently.

A stage that fails ends the pipeline and the failing stage is named in the error
(`planner.py:588-590`).

## Alternatives considered

- **One long session with internal subtasks.** Rejected: no artifact boundary between stages, no
  isolated reviewer, and no per-stage timeout. The same context would implement and verify.
- **A two-phase design→build split.** Rejected: the compliance matrix (ADR-0004) needs the numbered
  specification to exist before implementation, and it needs verification to re-read that same
  specification after the code changes.
- **Write artifacts into the Konstruktor output directory.** Rejected: these files describe the
  target project's change, so they belong to that project and belong in its pull request. Keeping
  them beside the run diagnostics would make them disposable at retention time
  (see ADR-0009).
- **Keep a shared transcript between stages.** Rejected: it would reintroduce the drift and the
  self-judging problem that separate sessions exist to prevent.

## Consequences

- Every stage leaves a human-readable artifact. After a run, the question "what exactly was asked
  for, and what was claimed" is answerable from the files alone.
- Because verification cannot see the implementation session, verification is an independent
  judgement rather than a recollection.
- No context is shared, so every stage prompt must be self-contained and every stage pays to
  re-read the project's state. Cost and wall-clock time scale with the number of stages.
- A plan run creates several agent sessions and therefore several run directories
  (see ADR-0009); disk usage and retention apply per session.
- Stages communicate only through files, which makes the pipeline resumable in principle but does
  not implement resume: `konstruktor resume` is a stub that exits 1 (`cli.py:290-297`).
- Adding a stage means: a prompt constant, an artifact freshness check (ADR-0005), a step call, and
  error handling that names it. `AGENTS.md` ("Planner Changes", "Planner Tests") carries the same
  requirement for anyone editing this code.

## Rationale provenance

Reconstructed. Recorded on 2026-10-17 for code from `5405324` (2026-09-08).

The pipeline structure, the per-stage session boundary, and the artifact paths are all directly
readable in `planner.py`. The rejection of a shared transcript and the comparison with a
"two-phase design→build split" were never written down; those alternatives are reconstruction from
the shape of the code, not a record of what was weighed. The "independent judgement" claim about
verification is a design intent visible in the code structure, not a measured result — nothing in
the repository measures whether separated sessions actually catch more failures than one long one.
