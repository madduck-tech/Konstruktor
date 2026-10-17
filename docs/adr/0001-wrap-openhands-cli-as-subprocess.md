# ADR-0001: Wrap the OpenHands CLI as a subprocess

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/runner.py:224-243`,
  `src/konstruktor/runner.py:214-221`, `src/konstruktor/runner.py:297-308`,
  `src/konstruktor/security.py:59`, `pyproject.toml:10-15`

## Context

Konstruktor has to drive an autonomous coding agent without a human in the loop. OpenHands is
available as a Python library and as a CLI. Both can produce a headless run; only one of them
defines a stable process boundary, a machine-readable event stream, and a kill story that belongs to
us rather than to the agent.

## Decision

Konstruktor does not depend on OpenHands as a Python library. It launches the `openhands` executable
as a subprocess in the user's working directory:

```text
openhands --headless --json --override-with-envs [--always-approve | --llm-approve] --task <task>
```

The agent is configured exclusively through environment variables set on that process: `LLM_API_KEY`,
`LLM_MODEL`, `LLM_BASE_URL` when a base URL is configured, and `RUNTIME` (`runner.py:214-221`).
`--override-with-envs` makes the agent prefer those variables over its own configuration. Approval
mode maps to CLI flags rather than to a config file.

`openhands` is a prerequisite that the user installs separately. Its absence is a distinct,
dedicated failure (exit code 5) with installation instructions, not a generic error
(`runner.py:297-308`). The child is started with `start_new_session=True` so that the entire agent
process tree can later be signalled as one group (`runner.py:235-243`).

## Alternatives considered

- **Import OpenHands as a library.** Rejected for the initial release: it would pull OpenHands' own
  dependency set into Konstruktor's install and bind our event parsing to its internal classes
  instead of to a documented JSON stream.
- **Talk to a hosted or remote agent API.** Rejected: it would add a service dependency, credentials
  to manage, and a network failure mode to a tool whose selling point is running on the user's
  machine.
- **Implement the agent loop ourselves.** Rejected: the agent is not the product; Konstruktor is the
  harness around it.

## Consequences

- The installed dependency set stays small and independent of OpenHands releases: `click`, `pyyaml`,
  `python-dotenv`, `structlog` (`pyproject.toml:10-15`).
- The process boundary gives a clean termination story — kill the group, not a thread — and makes
  timeouts enforceable from the outside.
- The event stream is the contract, so parsing must tolerate schema drift across OpenHands versions
  (`src/konstruktor/parser.py`). That tolerance is not optional polish; it is the cost of this
  boundary.
- Behavior is pinned to observed upstream behavior rather than to a declared version. Two comments
  name the version this code was written against: "OpenHands headless 1.21"
  (`security.py:59`) and the clean-exit-without-finish-event case (`parser.py:234-239`). An OpenHands
  upgrade can change Konstruktor's behavior without a change here, so upgrading it is a
  reviewable event, not a routine bump.
- Secrets are passed through the environment of the child process, which makes the run directory and
  the process table sensitive (see ADR-0009).

## Rationale provenance

Reconstructed. No design note was written when this was decided; the ADR is recorded on 2026-10-17,
seven weeks after the code landed in `5405324`.

The boundary itself is directly observable: the command line at `runner.py:224`, the absence of
OpenHands from `pyproject.toml`, the separate installation step in the README, and exit code 5.
The claims about dependency coupling and about remote APIs were not recorded anywhere and are
inference from what is absent in the repository, not a statement of what was weighed at the time.
