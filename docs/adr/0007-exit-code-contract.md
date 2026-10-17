# ADR-0007: Exit codes are a public contract

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `README.md:185-196` (published 2026-09-08,
  clarified in `623858c`); `src/konstruktor/runner.py:56-69`,
  `src/konstruktor/runner.py:571`, `src/konstruktor/parser.py:215-232`,
  `src/konstruktor/planner.py:1246`, `src/konstruktor/planner.py:1266`,
  `gopnik.json` (`stage2`)

## Context

Konstruktor runs headlessly: from CI (`examples/github-actions.yml`), from Kubernetes Jobs
(`deploy/k8s/`), and from shell scripts. Those callers branch on the exit status and cannot read
the console report. The set of failures is heterogeneous — bad input, a missing dependency, a
timeout, an unenforceable configuration — and a caller that cannot tell them apart can only retry
everything or nothing.

## Decision

The exit code is the primary machine interface, with a fixed vocabulary published in the README
(`README.md:185-196`):

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Task or verification failed |
| 2 | Invalid input or working directory |
| 3 | Timeout |
| 4 | Invalid configuration |
| 5 | OpenHands CLI not found |
| 6 | Requested runtime security controls cannot be enforced |
| 130 | Cancelled by signal |

The mapping is implemented in one place, `RunResult.exit_code` (`runner.py:56-69`), and returned by
both entry points: `run_task` returns the final attempt's code (`runner.py:594`) and `run_plan`
returns 0 only when the pipeline passed (`planner.py:1266`), forwarding the step's code when a stage
failed (`planner.py:1246`).

Two rules keep the vocabulary from being polluted:

- **Agent exit codes never become harness codes.** The subprocess return code is stored separately
  as `agent_exit_code`, and a non-zero one maps to harness code 1 (`parser.py:215-221`). A finish
  event's exit code is likewise kept in `agent_exit_code` and mapped to 0 or 1
  (`parser.py:225-232`). Otherwise an OpenHands convention could silently redefine our codes.
- **Codes the retry loop must not retry are enumerated, not inferred:** 3, 5, 6, and 130
  (`runner.py:571`). A timeout, a missing dependency, an unenforceable configuration, and a
  cancellation are not improved by running again.

## Alternatives considered

- **Boolean success/failure.** Rejected: a caller cannot distinguish "retry later" from "fix your
  config first", which is the entire practical value of the table.
- **Pass the agent's exit code through.** Rejected: it would collide with the harness vocabulary and
  make the table unstable across OpenHands versions.
- **Always 0, with the result only in the report.** Rejected: this is a CLI whose primary consumers
  are non-interactive.
- **Signal-based reporting for cancellations.** Rejected as the primary channel; 130 already reports
  cancellation through the conventional code, and the process still receives the signal.

## Consequences

- Automation can branch on the failure class without parsing text. This is not theoretical: the
  Stage 2 check in `gopnik.json` drives the CLI with a missing directory and asserts that the
  process exits with exactly 2.
- The table is a published interface. Adding a failure mode means extending it in code and in
  `README.md`, and consumers that switch on specific codes keep working — but any change to an
  existing code's meaning is a breaking change.
- Exit code 1 covers several distinct failures — the task failed, verification failed, an unexpected
  exception, a non-zero agent exit. Callers needing the distinction must read `summary.json` or the
  report (see ADR-0009).
- Every command calls `sys.exit(...)` explicitly rather than returning, including commands that
  cannot fail (`cli.py:128`, `cli.py:223`, `cli.py:297`), so the code path is uniform.
- `resume` exists as a command and exits 1 (`cli.py:290-297`). That is honest — the feature is
  unimplemented — but it means "not implemented" is indistinguishable by code from "task failed".

## Rationale provenance

Partly recorded. The table is published in the README, and `gopnik.json` consumes exit 2
mechanically, so the contract is externally visible rather than merely internal.

Recorded on 2026-10-17 for a contract introduced in `5405324` (2026-09-08) and clarified in
`623858c` (2026-10-15), which is the commit that made the fail-closed and exit-code behavior explicit
in the README.

The rejected alternatives were not recorded and are reconstruction from the code's shape. The
enumeration of non-retryable codes at `runner.py:571` exists in code but its rationale — that these
four are terminal rather than transient — is inferred, not stated.
