# AGENTS.md - Instructions for AI agents working on Konstruktor

## Project Overview

**Konstruktor** is a Python CLI wrapper around OpenHands. Its tagline is: "Autonomous agent that
turns problems into working solutions."

It has two primary modes:

- `run`: execute one headless coding task and record its result.
- `plan`: run Intent -> Spec -> Review -> Plan -> Implement -> Verify -> Fix -> Report.

## Engineering Guidance

### Planner Changes

`src/konstruktor/planner.py` is the core of the spec-driven pipeline. It contains the prompts for
each step, compliance and verdict parsers, the `Planner` class, and `run_plan()`.

Prompt changes affect another agent's behavior. Keep prompts explicit, deterministic, and aligned
with the parser contracts. In particular, preserve numbered `R`, `A`, and `O` items and the exact
compliance matrix statuses expected by the parser.

### Planner Tests

Planner tests are in `tests/test_planner.py`. They mock `KonstruktorRunner.run` to simulate agent
output. Common patterns include:

- `_make_success_result(msg)` for a successful agent step.
- `patch.object(Path, "exists", return_value=True)` for artifact existence.
- `patch.object(Path, "read_text", return_value="...")` for artifact content.
- `patch.object(Path, "write_text")` for artifact writes.

Cover at least the happy path, successful fix path, exhausted fix cycles, malformed agent output,
and missing or stale artifacts when changing pipeline behavior.

### CLI Changes

`src/konstruktor/cli.py` contains all Click commands. When adding an option, update the
`@click.option` declaration, command function signature, and call to `run_task()` or `run_plan()`.

### Configuration

- Packaged defaults: `src/konstruktor/config.default.yaml`
- User override: `~/.konstruktor/config.yaml`
- Environment prefix: `KONSTRUKTOR_*`
- Merge order: packaged defaults -> user file -> environment -> CLI flags
- Default output: `konstruktor-output`
- Default sandbox: `process`

Configuration files are optional and are not created automatically. Keep config documentation,
validation, and tests synchronized when changing the schema.

### Security

The `process` sandbox is the only supported headless runtime and does not provide container
isolation. Unsupported sandbox modes, approval modes, and command policies must fail closed rather
than claim controls that cannot be enforced.

### Verification

Run before committing:

```bash
pytest -v
ruff check src/ tests/
```

## Decision Records

Architecture decisions live in `docs/adr/`. `docs/adr/README.md` holds the process, the trigger
criteria, the template, and the index.

Write an ADR when the change is hard to reverse, changes a public contract (CLI surface, exit codes,
artifact layout, configuration schema, parsed event shapes), concerns security or isolation, or
picks between alternatives a newcomer would reasonably challenge. Do not write one for bug fixes,
formatting, refactors with equivalent tests, or routine dependency bumps.

The ADR is opened as `Proposed` before or in the same pull request as the change, and flips to
`Accepted` in that same pull request. A decision that turns out to be wrong is superseded by a new
ADR; accepted ADRs are never edited after the fact.

Every factual claim about current behavior cites `file:line`, and the commit that introduced the
decision is recorded in the `Evidence` field. Where the original rationale was never written down,
say so in *Rationale provenance* instead of reconstructing it as fact.

## File Map

```text
src/konstruktor/
|-- cli.py          # Click commands
|-- config.py       # YAML and environment configuration
|-- logger.py       # Structured logging
|-- parser.py       # OpenHands JSONL event parsing
|-- planner.py      # Spec-driven pipeline
|-- reporter.py     # Text and JSON output
|-- runner.py       # OpenHands subprocess runner
`-- security.py     # Runtime security validation

tests/
|-- test_adr_docs.py
|-- test_cli.py
|-- test_config.py
|-- test_parser.py
|-- test_planner.py
|-- test_reporter.py
|-- test_runner.py
`-- test_security.py
```

## Architecture Decisions

Reasoning behind the current design is recorded in `docs/adr/`, starting with
`docs/adr/README.md`. The ten foundational records are 0001 through 0010.

## Plan Artifacts

For `konstruktor plan -t "Add feature"`, the agent writes:

```text
<working-directory>/docs/specs/add-feature/
|-- intent.md
|-- spec.md
|-- plan.md
|-- compliance.md
`-- report.md
```

Each pipeline session also writes diagnostics under `konstruktor-output/run-.../` by default.
