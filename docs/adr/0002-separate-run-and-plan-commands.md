# ADR-0002: Separate `run` and `plan` top-level commands

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/cli.py:88`,
  `src/konstruktor/cli.py:172`, `src/konstruktor/cli.py:186-196`,
  `src/konstruktor/runner.py:514-527`, `src/konstruktor/planner.py:1142-1156`

## Context

Konstruktor serves two different requests. One is "here is a task, go do it, tell me what you
changed". The other is "here is a goal, work it out, implement it, and prove it was done". They
differ in what they produce, what limits them, and what a caller has to inspect to find out what
happened.

## Decision

Two top-level commands, not one command with a mode flag:

- `konstruktor run` executes one headless session per attempt, with an optional retry loop
  (`max_retries`), and writes diagnostics.
- `konstruktor plan` executes the spec-driven pipeline (ADR-0003) and writes both diagnostics and
  spec artifacts.

`run` does not delegate to `plan`, and `plan` does not call `run`; both call `KonstruktorRunner`
directly. The distinction is visible in the shape of each command's options: `run` exposes
`--max-retries`, `plan` exposes `--max-iterations` and `--max-fix-cycles`, and neither exposes the
other's limits.

## Alternatives considered

- **One command with `--plan` / `--mode`.** Rejected: a flag implies one implementation with two
  configurations. These are two implementations with different artifact contracts, different
  failure semantics, and different reports. A flag would have hidden that behind a branch.
- **Implement `run` as a degenerate `plan`.** Rejected: a single-task run would then have to author a
  specification and a compliance matrix before touching any code, paying for a ceremony that only
  makes sense when the task was ambiguous.
- **A single `konstruktor task` command with everything mandatory.** Rejected: the common case
  (one task, no ceremony) would drag every user through the pipeline's surface.

## Consequences

- Each command has a small, readable surface, and its help text can describe what it actually does
  (`cli.py:88-113`, `cli.py:172-203`).
- `plan` can grow stages and prompts without changing `run`, and vice versa.
- Shared options are declared twice in `cli.py` and must be kept in sync by hand: `-t/--task`,
  `-d/--directory`, `-c/--config`, `-m/--model`, `--timeout`, `-o/--output`, `--json`, `--dry-run`,
  `-v/--verbose`, `--approve`. Adding an option to one command does not add it to the other.
- That duplication already has a cost: `run` accepts repeatable `-e/--env KEY=VALUE` overrides and
  `plan` does not (`cli.py:80-87` has no counterpart in the `plan` signature, `cli.py:172-185`). A
  plan run therefore cannot forward extra environment variables to the agent.
- `run`'s `--timeout` help text says only "Timeout in seconds (default from config)"
  (`cli.py:56-59`), while `plan`'s says "Timeout in seconds per step" (`cli.py:144-146`). Neither
  describes the code: the value is applied to the whole retry loop for `run` and to the entire
  pipeline for `plan` (`planner.py:672-677`). The help text is imprecise and the code is
  authoritative.

## Rationale provenance

Reconstructed from the code and the CLI documentation, recorded on 2026-10-17 for code that landed
in `5405324` on 2026-09-08.

The structural facts are verifiable: two Click commands, two entry points (`run_task`,
`run_plan`), distinct option sets. The reason for choosing two commands over a flag was never
written down; the rejection rationales above are reconstruction from what the code does not share,
not a record of what was considered.
