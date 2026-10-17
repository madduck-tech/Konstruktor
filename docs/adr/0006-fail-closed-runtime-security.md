# ADR-0006: Fail closed on security controls that cannot be enforced

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/security.py:58-82`,
  `src/konstruktor/runner.py:205-211`, `src/konstruktor/config.default.yaml:28-32`,
  `src/konstruktor/config.py:107-109`, `README.md:174-183`

## Context

Konstruktor runs a non-interactive agent that executes shell commands as the current user. Its
configuration exposes `agent.sandbox`, `agent.mode`, and a `security` block with
`allowed_commands`, `blocked_commands`, and `require_approval_for`. A user reading that schema
reasonably concludes that setting `agent.sandbox: docker` gives them containers and that
`blocked_commands: ["sudo"]` gives them a blocklist.

The headless OpenHands CLI does not enforce all of that. Passing the values through would produce a
configuration that reads as a guarantee and is not one.

## Decision

Only one configuration is supported: `agent.sandbox: process`, `agent.mode: auto`, and empty
command and approval lists. Everything else fails **before the agent starts**, with exit code 6 and
a message naming the setting that cannot be honored (`security.py:58-82`, `runner.py:205-211`):

- a sandbox other than `process` → refused, naming the value;
- an approval mode other than `auto` → refused, naming the value;
- any non-empty `allowed_commands`, `blocked_commands`, or `require_approval_for` → refused.

The refusal is a refusal, not a downgrade: the agent does not start, and the run is recorded with
status `error` and exit code 6 so it stays diagnosable (`runner.py:207-211`).

Two supporting commitments:

- **The packaged defaults are the supported configuration.** `src/konstruktor/config.default.yaml`
  ships the passing values, and the `security` block carries an explicit comment that command
  policies must stay empty until the CLI can enforce them
  (`src/konstruktor/config.default.yaml:28-32`, `:31`).
- **The README states the limit plainly.** The `process` sandbox is described as OpenHands' local
  runtime, explicitly "not container isolation", and the list of fail-closed settings is published
  rather than left to be discovered by hitting them (`README.md:174-183`).

Note that the configuration *schema* is deliberately wider than what is supported: `validate_config`
accepts `docker`, `process`, and `remote` (`config.py:107-109`). Accepting the value at the schema
level and refusing it at the runtime level keeps one error message per failure class instead of two
validation paths.

## Alternatives considered

- **Pass `docker` / `remote` through to OpenHands.** Rejected: Konstruktor cannot verify that the
  value produces isolation, so accepting it would advertise a containment guarantee it does not
  provide. This is the specific failure the decision exists to prevent.
- **Implement command filtering in Konstruktor.** Rejected: the harness does not see the commands
  the agent executes. `check_blocked_command` and `requires_approval` (`security.py:6-37`) exist as
  helpers and are exercised by `tests/test_security.py`, but nothing calls them on the execution
  path — they are not a filter, and this ADR does not claim they are one.
- **Warn and continue.** Rejected: a warning nobody reads is not a control, and it converts a
  blocked configuration into a silently weaker run.
- **Reject every non-empty `security` block outright, including empty-list overrides.** Rejected:
  rejecting an explicitly empty list would fail users whose config was written against the schema.

## Consequences

- The tool never implies containment it cannot honor. A user who wants container isolation is told
  so, with the exit code that says why.
- Exit code 6 is distinct from 1 and 4 so automation can separate "your configuration asks for
  something unenforceable" from "the task failed" and "the configuration is invalid"
  (see ADR-0007). Retries do not apply: `run_task` breaks out of the retry loop on 6
  (`runner.py:571`).
- Users with `agent.sandbox: docker` in their config are blocked outright, even though the value is
  schema-valid. This is a deliberate, user-visible break in exchange for not lying about isolation.
- `security.py` carries helpers that are not on the enforcement path. They are dead weight on the
  live path and can be misread as a working filter; anyone considering deleting them should note
  that they are the only executable expression of the command-policy idea so far.
- The default `agent.sandbox` differs between modules: `config.py:107` falls back to `"docker"` and
  `runner.py:60` falls back to `"process"`. In practice the packaged default is always present, so
  the fallback rarely fires, but the two disagree.
- Every environment that needs real isolation must provide it outside Konstruktor — a container, a
  disposable VM, or an ephemeral job. `deploy/` contains recipes for exactly that shape of isolation
  (see the tier-2 candidates in `docs/adr/README.md`).

## Rationale provenance

Partly recorded. The fail-closed behavior is stated in the code itself — `security.py:59` documents
it as a decision about "controls unsupported by OpenHands headless 1.21",
`src/konstruktor/config.default.yaml:31` carries the comment, and `AGENTS.md` ("Security") states
the rule for anyone editing this code. The README's Security section was written at the same time as
the code in `5405324`.

The rejected alternatives and the reasoning about the schema-versus-runtime split were not recorded
and are reconstruction. The claim that the helpers are "not a filter" is verified: a repository-wide
search finds `check_blocked_command` and `requires_approval` referenced only in
`tests/test_security.py`, never in `src/`.
