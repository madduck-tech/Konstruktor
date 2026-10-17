# ADR-0005: Written artifacts are the source of truth

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/planner.py:564-585`,
  `src/konstruktor/planner.py:588-590`, `src/konstruktor/planner.py:128`,
  `src/konstruktor/planner.py:200`, `src/konstruktor/planner.py:261`,
  `src/konstruktor/planner.py:376`, `src/konstruktor/planner.py:832`,
  `src/konstruktor/planner.py:893-894`

## Context

A stage can exit successfully and have produced nothing. An agent asked to write `spec.md` may
decide the file already contains what it wants and exit 0. If exit status is taken as proof of work,
that stage passes silently and the pipeline builds on an empty file.

## Decision

Success of an artifact-producing stage is defined by the file on disk, not by the agent's exit
status or its final message.

After the stage, the harness requires all three (`planner.py:572-585`):

1. the file exists;
2. its content is non-empty after stripping whitespace;
3. it was created or updated **during that stage**, proven by comparing a four-part filesystem
   identity captured before the stage started: `(st_ino, st_size, st_mtime_ns, st_ctime_ns)`
   (`planner.py:564-569`).

Any failure produces an error naming the step and the path: "Specification was not created at ...",
"Specification artifact is empty at ...", "Specification did not create or update ...". A stage that
exited 0 without a fresh artifact is a failure (`planner.py:588-590`).

Two supporting rules:

- **Prompts ask for an unconditional write.** Every artifact-producing prompt ends with "Always write
  or overwrite the target file, even when the existing content is identical"
  (`planner.py:128`, `:200`, `:261`, `:376`). This is what makes rule 3 achievable at all: an agent
  that correctly concludes "no change needed" must still rewrite the file.
- **Stale artifacts are deleted, not trusted.** Before each verification the previous
  `compliance.md` is removed (`planner.py:832`, `:893-894`), so a matrix from an earlier attempt can
  never satisfy the freshness check and be mistaken for a fresh result.

The filesystem identity is also used to detect a review stage that claims a fix while leaving the
file untouched (`planner.py:761-764`).

## Alternatives considered

- **Exit status only.** Rejected: it cannot distinguish "wrote the file" from "decided it was already
  written", and the second case is the failure this decision exists to catch.
- **Hash the file content.** Rejected for now: freshness plus non-emptiness covers the failure modes
  that matter here, and content hashing adds false failures when an agent rewrites byte-identical
  content with different metadata. If identical rewrites start failing for a different reason, this
  is the first thing to revisit.
- **Ask the agent to confirm it wrote the file.** Rejected: that is self-report again, with an extra
  step.
- **Check only that the file exists.** Rejected: an artifact left over from a previous run satisfies
  existence forever.

## Consequences

- Silent no-ops become visible, named failures pointing at the exact path.
- Every artifact-producing stage must capture the before-state and check it afterwards. Adding a
  stage means adding this check; forgetting it silently reinstates the original failure mode.
- Freshness is inferred from filesystem metadata, which is evidence, not proof. The four fields
  narrow the gaps — inode and ctime catch rewrites on most filesystems, nanosecond mtime catches fast
  rewrites — but copy-on-write filesystems, coarse timestamps, or an external process touching the
  file can still confuse it.
- Identical content rewritten by the agent is accepted. Freshness is not equality; a stage that
  rewrites what it wrote is not penalized.
- The prompts carry an instruction that looks redundant ("always write even if identical"). It is
  load-bearing, and weakening it as noise would break the contract at
  `planner.py:572-585`.

## Rationale provenance

Reconstructed. Recorded on 2026-10-17 for code from `5405324` (2026-09-08).

The mechanism is fully verifiable in `planner.py`, including the four metadata fields, the three
failure messages, and the unconditional-write instruction in all four artifact prompts. What was
never written down: why those four fields and not a hash, whether the metadata check was chosen
before or after a specific failure was observed, and whether the exact-inode check has ever produced
a false failure in practice. The last one is unmeasurable from the repository and remains an open
question for whoever tunes this.
