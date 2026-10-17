# ADR-0010: Timeout, step-idle and step-count limits

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/runner.py:319-427`,
  `src/konstruktor/runner.py:429-452`, `src/konstruktor/runner.py:380-395`,
  `src/konstruktor/runner.py:359-362`, `src/konstruktor/config.default.yaml:6-10`,
  `src/konstruktor/runner.py:242`

## Context

An autonomous agent can hang, stall without producing output, or loop. A harness that does not
bound execution cannot be scheduled, and a harness that kills only the process it spawned leaves the
agent's children running.

## Decision

Three independent limits, all from configuration (`src/konstruktor/config.default.yaml:6-10`), plus
a single termination path.

- **`limits.timeout`** — wall-clock bound on the whole attempt. For `plan` it is bound once for the
  entire pipeline and each stage receives the remaining budget (`planner.py:677`, `:1065-1073`).
- **`limits.step_timeout`** — bound on the **gap between consecutive parsed stdout events**. It is an
  idle timeout, not a per-step timeout: it measures silence, and it is reset by any parsed event,
  including observations (`runner.py:366`, `:411`).
- **`limits.max_steps`** — bound on the number of agent action events, counting actions from the
  agent source and excluding finish events (`parser.py:85-89`, `runner.py:412-418`).

Two details make the limit loop workable:

- **Both pipes are drained concurrently.** Reader threads feed one queue, so a chatty stderr can
  never block stdout, and neither pipe can fill and deadlock the agent (`runner.py:326-348`).
  stdout is parsed as it arrives; stderr is captured verbatim for `agent-error.log`.
- **Termination kills the process group.** The child is started with `start_new_session=True`
  (`runner.py:242`), and termination signals the whole group: `SIGTERM` to the process group, then
  `SIGKILL` to the group after 5 seconds, then the process itself as a last resort
  (`runner.py:429-452`). After any termination the loop allows a 1-second drain window to collect
  trailing output before giving up (`runner.py:380-395`).

Termination is triggered by any of the three limits or by a signal. `SIGINT` and `SIGTERM` handlers
are installed only when the runner owns the main thread, and the original handlers are restored on
exit, so embedding the runner in another program does not hijack that program's signals
(`runner.py:359-362`, `:420-423`). Signal-driven cancellation is exit code 130 (see ADR-0007);
a limit-driven stop is 3 for either timeout, and 1 for `max_steps`
(`runner.py:257-272`).

## Alternatives considered

- **Rely on the agent's own timeouts.** Rejected: an agent that stops emitting events cannot enforce
  its own deadline, and the harness would inherit the hang.
- **A wall-clock cap per agent action.** Rejected: the duration of a step is unknown while it is
  running. The only observable signal from outside is the absence of events, which is what
  `step_timeout` uses.
- **Kill only the direct child.** Rejected: OpenHands spawns children — shells, language servers —
  that would survive and keep writing into the working directory.
- **A single `max_steps`-style limit for everything.** Rejected: wall time and step count fail in
  different ways (a stalled stream versus a loop) and need different thresholds.
- **Hard `SIGKILL` immediately.** Rejected: `SIGTERM` first lets the agent flush output, which is
  frequently the evidence needed to diagnose why it was killed.
- **Install signal handlers unconditionally.** Rejected: `signal.signal` raises off the main thread,
  and taking over a host program's handlers is not this tool's call.

## Consequences

- No run exceeds its budget, and the agent's process tree does not leak on termination.
- `step_timeout` measures the harness's view of progress, not the agent's. An agent legitimately
  working for minutes without emitting an event is killed. The default of 300s
  (`src/konstruktor/config.default.yaml:8`) is the tuning surface for this; lowering it trades fewer
  false kills for more real hangs.
- The three limits are not independent in practice. `max_steps` firing is reported as a failure
  (exit 1), not a timeout, because the run did not exceed its time budget — a caller retrying
  "timeouts" will not retry it (`runner.py:265-268`).
- The 1-second drain window can truncate the tail of the agent's output, including the message that
  would have explained the failure.
- Effective runtime is the product of three limits and the retry backoff in `run_task`
  (`runner.py:574-577`), so changing one limit changes the worst-case wall time of a retried run.
- Signal handling is restricted to the main thread. Under a threaded host, cancellation falls back to
  the limits rather than to `SIGINT`.

## Rationale provenance

Reconstructed. Recorded on 2026-10-17 for code from `5405324` (2026-09-08).

The three limits and their defaults are in the packaged configuration and the enforcement loop; the
1-second drain window is commented at `runner.py:394`. The distinction between "idle timeout" and
"per-step timeout" is visible in how `last_event` is maintained (`runner.py:366`, `:411`) but was
never named in a comment or document, and `run --help` describes `--timeout` without mentioning that
`step_timeout` is a separate, event-based bound.

The rejected alternatives and the reasoning about drain-window truncation were not recorded and are
reconstruction. No measurement of false kills from `step_timeout` exists in the repository, so the
default of 300 seconds is a chosen value, not a calibrated one.
