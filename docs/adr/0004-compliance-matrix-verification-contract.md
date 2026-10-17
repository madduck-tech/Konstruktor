# ADR-0004: Verification is a machine-parsed compliance matrix

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/planner.py:285-318`,
  `src/konstruktor/planner.py:412-461`, `src/konstruktor/planner.py:497-530`,
  `src/konstruktor/planner.py:533-551`, `src/konstruktor/planner.py:850-852`,
  `src/konstruktor/planner.py:59-66`, `src/konstruktor/planner.py:464-494`,
  `src/konstruktor/planner.py:867`

## Context

"Did the agent do it?" cannot be answered from prose, and it cannot be answered by the agent that
wrote the code. Verification has to produce something a parser can check, or the whole pipeline
reduces to "the agent said it passed".

## Decision

The pass/fail signal is a markdown compliance matrix over numbered identifiers, parsed by the
harness.

**The specification side.** The spec must contain three required sections with numbered items:
`Functional Requirements` (`R1..Rn`), `Acceptance Criteria` (`A1..An`), and `Out of Scope`
(`O1..On`). The harness extracts those IDs itself (`planner.py:497-530`) and refuses to continue if a
section is missing or an ID family is absent.

**The verification side.** The verify stage must emit a table with exactly those IDs, one row per
ID, a status from `✅ Done` / `❌ Not done` / `⚠️ Partially done`, and non-empty evidence — a file
path, line number, test name, or command output (`planner.py:285-318`). An item counts as done only
when the status is `✅ Done` **and** evidence is non-empty (`planner.py:34-35`).

**The comparison.** A run passes only when all of the following hold (`planner.py:533-551`,
`planner.py:847`):

- the matrix's ID set equals the spec's ID set — no missing rows, no extra rows;
- neither side contains duplicate IDs;
- every item is `✅ Done` with evidence;
- the agent printed exactly one parseable `VERDICT: PASS` or `VERDICT: FAIL`.

Anything else is a failure. Zero parsed verdicts ("Could not parse verification verdict"), two
contradictory verdicts, and an unparseable matrix are all failures, never passes
(`planner.py:464-494`).

**Consequences of the rule, spelled out:**

- `compliance.md` is written only when coverage is exact (`planner.py:850-852`). A partial matrix
  leaves no compliance file behind rather than a misleading one.
- Only explicit failures drive the fix loop. Malformed rows — statuses that are not one of the three
  — are excluded from `concrete_failures` (`planner.py:59-66`), so a garbled matrix does not send the
  fixer chasing phantoms.
- The fix budget is `min(max_fix_cycles, max_iterations - 1)` (`planner.py:867`): fix cycles are
  capped twice, and the effective count is the lower of the two.
- Re-verification after a fix must regenerate the **full** matrix, not only the rows that failed
  (`planner.py:895-902`).

## Alternatives considered

- **Trust the agent's `VERDICT: PASS`.** Rejected: a verdict is prose plus a claim. Nothing in it
  distinguishes "implemented and checked" from "believed it was implemented".
- **Accept partial coverage.** Rejected: a matrix missing `R7` is exactly how a skipped requirement
  becomes invisible.
- **Verify by executing the project's own test suite.** Rejected as the *only* mechanism: the target
  project may have no tests, and the spec's acceptance criteria are not always test expressions. It
  remains a valid source of evidence for a row.
- **Machine-readable JSON from the agent instead of markdown.** Rejected for the initial release: it
  removes a fragile surface, but it also constrains the model's output format far more tightly, and
  a constrained format failure is a harder failure to diagnose than a missing table row. Worth
  revisiting if matrix parsing proves unreliable in practice.

## Consequences

- The pass/fail signal is mechanical and reproducible from the artifacts, not from the agent's
  account of itself.
- Failures are localized to an ID, which is what makes a targeted fix prompt possible
  (`planner.py:876-881`).
- **Strictness produces false negatives.** A correct implementation reported with a missing,
  renamed, or duplicated row fails. That is a deliberate trade: a pipeline that occasionally fails
  a working change is recoverable, and one that occasionally passes a broken change is not.
- The harness depends on a fragile surface — a markdown table, specific emoji statuses, and a verdict
  line. Prompts and parser must change together; `AGENTS.md` ("Planner Changes") records that
  coupling, and `tests/test_planner.py` is the only guard against them drifting apart.
- Emoji status normalization (`planner.py:447-452`) makes the contract dependent on Unicode handling
  in both directions.
- Nothing in the repository measures how often the strictness causes a false failure, so the cost
  side of this trade-off is unquantified.

## Rationale provenance

Partly reconstructed. Recorded on 2026-10-17 for code from `5405324` (2026-09-08).

The rules themselves are unambiguous in the code and in the prompts. Two things are reconstruction:
the rejected JSON alternative was never proposed in writing, and the claim that partial coverage was
rejected specifically because it hides skipped requirements is an inference from the exact-coverage
check, not a recorded statement. No measurement of the false-negative rate exists anywhere in the
repository.
