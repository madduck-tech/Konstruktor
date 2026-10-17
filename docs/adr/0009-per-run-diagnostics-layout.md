# ADR-0009: Per-run diagnostics directory with bounded retention

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/runner.py:135-137`,
  `src/konstruktor/runner.py:454-472`, `src/konstruktor/runner.py:474-490`,
  `src/konstruktor/reporter.py:12-64`, `src/konstruktor/logger.py:49-64`,
  `src/konstruktor/runner.py:586-593`, `README.md:156-172`, `.gitignore:11`

## Context

A headless run produces output nobody is watching. When something goes wrong, the console is gone
and the only evidence is what was written to disk. Diagnostics have to survive the run, be
attributable to a specific attempt, and not grow without bound on a machine that runs many.

## Decision

Every attempt writes to its own directory under the output base
(`runner.py:135-137`):

```text
konstruktor-output/run-<UTC YYYY-MM-DD-HHMMSS>-<6 hex>/
|-- summary.json      # RunSummary.to_dict(): status, exit code, steps, duration, files changed
|-- events.jsonl      # raw agent event lines, one JSON object per line
|-- agent.log         # agent stdout, verbatim
|-- agent-error.log   # agent stderr; written only when non-empty
|-- diff.patch        # what changed in the working directory
`-- harness.log       # Konstruktor's own structured log (structlog)
```

Four properties make this useful:

- **The run id is unique and ordered.** UTC timestamp plus six hex characters of UUID
  (`runner.py:135-136`) — sortable by mtime and collision-free enough that a retried task gets its
  own directory rather than overwriting the previous attempt.
- **Artifacts are written on every exit path**, including startup failures such as a missing working
  directory or a missing OpenHands CLI (`runner.py:152`, `:210`, `:307`, `:316`). A run that never
  started is still diagnosable.
- **The diff covers untracked files.** `git diff HEAD` alone would miss every new file the agent
  created, which is most of the interesting output, so each untracked path is additionally rendered
  as a `git diff --no-index` patch — and paths inside the output directory are excluded, so the
  diagnostics never appear inside the diagnostics (`reporter.py:21-64`).
- **Retention is count-based.** After a successful attempt, all but the newest
  `logging.keep_runs` directories are removed (`runner.py:474-490`).

Two channels, deliberately separated: the human report goes to **stdout**, structured logs go to
**stderr** and to `harness.log` (`logger.py:49-64`), and `--json` replaces the stdout report with
JSON while stderr stays human-readable (`runner.py:586-593`). `konstruktor run -t ... --json | jq .`
therefore works without filtering noise.

Plan artifacts are deliberately **not** here: `intent.md`, `spec.md`, `plan.md`, `compliance.md`,
and `report.md` are written into the target project (ADR-0003). The run directory holds evidence;
the project holds deliverables.

## Alternatives considered

- **One log file for all runs.** Rejected: a retried or concurrent run would interleave, and nothing
  could be attributed to an attempt.
- **Symlink or truncate on each new run.** Rejected: the previous run is the one being debugged.
- **Keep only the summary and drop the event stream.** Rejected: the raw stream is the only way to
  reconstruct what the agent actually did; the summary is a conclusion, not evidence.
- **Retain by age.** Rejected: a count is predictable and matches the disk budget a user has in
  mind; "delete after 30 days" is not.
- **Prune on startup.** Rejected as a variant: pruning after a successful run means cleanup happens
  only when things work, which is when disk has actually been consumed.
- **Write plan artifacts into the output directory.** Rejected: see ADR-0003.

## Consequences

- Every attempt is independently diagnosable; nothing depends on a terminal that is long gone.
- The directory layout is a de facto interface: `konstruktor status` and `konstruktor list` read
  `summary.json` out of these directories and select by `run-` prefix (`cli.py:263-287`,
  `cli.py:314-337`). Renaming or moving a file breaks those commands.
- **Diagnostics contain sensitive material.** `events.jsonl` and `diff.patch` hold full source
  content of the working directory. The output base is git-ignored by default (`.gitignore:11`,
  `.gitignore:12`) but a custom `--output` or `KONSTRUKTOR_LOG_DIR` can point anywhere.
- Retention is destructive. On a machine running many tasks, only the newest `keep_runs` attempts
  survive; the default is 10 (`src/konstruktor/config.default.yaml:26`). Cleanup failures are
  swallowed silently (`runner.py:487-489`).
- Pruning is driven by whichever attempt got far enough to reach it. Attempts that return before
  `runner.py:280` — a missing working directory (`:140-153`), an unenforceable configuration
  (`:205-211`), a missing CLI (`:297-308`), an unexpected exception (`:310-317`) — write their
  directory and leave without pruning. A run that streams to completion prunes regardless of whether
  it passed, so a repeatedly failing task still gets its history trimmed by each failing attempt; a
  task that fails before streaming accumulates directories until some later run prunes them.
- `agent-error.log` exists only when the agent wrote to stderr, so its absence is meaningful rather
  than a collection gap.
- Two run directories exist per plan run: one per stage session plus the aggregate summary written
  into the last session's directory (`planner.py:1234-1235`).

## Rationale provenance

Reconstructed. Recorded on 2026-10-17 for code from `5405324` (2026-09-08).

The layout, file names, run-id format, and retention rule are directly readable in `runner.py`,
`reporter.py`, and `logger.py`, and published in `README.md:156-172`. The stdout/stderr split is
visible but was never justified in writing; the rationale above is inference from `--json` being a
usable pipe.

Two statements in this ADR are findings from inspection on 2026-10-17, not recorded decisions: that
pruning is skipped by attempts that return before `runner.py:280`, and that a plan run produces one
directory per stage. Neither was previously documented anywhere.
