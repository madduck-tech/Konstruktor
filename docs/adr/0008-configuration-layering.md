# ADR-0008: Configuration layering with an optional user file

- **Status:** Accepted
- **Date:** 2026-10-17
- **Deciders:** Alexey Kalyaganov
- **Evidence:** `5405324` (2026-09-08); `src/konstruktor/config.py:41-90`,
  `src/konstruktor/config.py:17-38`, `src/konstruktor/config.py:93-127`,
  `src/konstruktor/config.py:12`, `src/konstruktor/config.default.yaml`,
  `config.default.yaml`, `README.md:111-154`

## Context

Users need to change a model, a limit, or an output path without editing packaged files. CI needs
to inject an API key without writing one to disk. The package must produce correct defaults when
installed as a wheel, where the source tree layout is not available.

## Decision

Four layers, each merged over the previous one (`config.py:41-90`):

1. **Packaged defaults.** Read from `src/konstruktor/config.default.yaml` through
   `importlib.resources` (`config.py:45-48`), so the file travels inside the wheel rather than
   depending on a path relative to the source tree. It is force-included into the wheel target
   (`pyproject.toml:34-35`).
2. **Optional user file.** `~/.konstruktor/config.yaml` by default
   (`config.py:12`), or the path given to `--config`. Deep-merged over the defaults
   (`config.py:30-38`, `:51-55`).
3. **Environment overrides.** Prefix `KONSTRUKTOR_*`, mapped per key
   (`config.py:64-83`). The legacy unprefixed `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL` are also
   read, and the prefixed names take precedence (`config.py:69-79`).
4. **CLI flags.** Applied last, in the command functions rather than in the loader.

After merging, `${VAR}` placeholders in any string value are substituted from the environment
(`config.py:17-27`, `:87-88`), which is how the packaged default points `llm.api_key` at an
environment variable instead of shipping a secret reference.

Two rules about the file itself:

- **The configuration file is never created automatically.** A missing user file is not an error and
  not an invitation. This is stated in `README.md:111-114`, in `AGENTS.md` ("Configuration"), and is
  the behavior of `config.py:51-55`.
- **`src/konstruktor/config.default.yaml` is the single source of truth.** The annotated copy at the
  repository root is documentation for humans reading the file. `tests/test_adr_docs.py` asserts
  that both parse to the same values, so the copy cannot drift.

Validation collects every problem and reports them all at once, exiting 4 on any of them
(`config.py:93-127`).

## Alternatives considered

- **Environment variables only.** Rejected: not human-editable, no comments, no structure for list
  values.
- **Write a configuration file on first run.** Rejected: a tool that silently creates files in the
  home directory is surprising, and it makes drift between two sources hard to diagnose — the file
  would be a stale copy of defaults the user never asked for.
- **Read packaged defaults from a path relative to `__file__`.** Rejected: breaks when the package is
  installed as a zip-imported wheel and is why `importlib.resources` is used instead.
- **TOML or JSON.** Rejected for now: YAML was chosen so the hand-edited file can carry comments.
  Switching formats is a breaking change to every existing user configuration.
- **Drop the legacy `LLM_*` names.** Rejected: they are the variables OpenHands itself uses, and
  users migrating from a direct OpenHands workflow already have them set.

## Consequences

- Precedence is total, ordered, and testable; `tests/test_config.py` covers the merge.
- Secrets come from the environment, so no key needs to be written to disk. `konstruktor config`
  masks the API key when printing, and only when it is longer than 8 characters
  (`cli.py:239-243`) — a shorter value is printed in full.
- `${VAR}` resolves to an empty string when the variable is unset (`config.py:21`), not to an error
  at substitution time. The empty API key is caught later by `validate_config` as exit code 4
  (`config.py:97-101`), so the failure is reported — but it is reported as a configuration error
  about a missing key, not as an unset placeholder.
- Only three `KONSTRUKTOR_*` variables are supported (`KONSTRUKTOR_LLM_API_KEY`,
  `KONSTRUKTOR_LLM_MODEL`, `KONSTRUKTOR_LLM_BASE_URL`, plus `KONSTRUKTOR_TIMEOUT` and
  `KONSTRUKTOR_LOG_DIR`). Every other setting is file-or-flag only, which is a real asymmetry for
  CI that wants to drive limits from the environment.
- `--env KEY=VALUE` on `run` (`cli.py:80-87`) passes variables to the agent process only. It is not a
  configuration layer and does not affect any setting: `run_plan` has no such option (see ADR-0002).
- The schema accepts values the runtime refuses — `agent.sandbox: docker` validates and then fails
  closed with exit 6 (see ADR-0006).
- `agent.skills_dir` is declared in both copies of the defaults
  (`src/konstruktor/config.default.yaml:15`, `config.default.yaml:24`) and read by nothing in
  `src/`. It is part of the published schema with no consumer.

## Rationale provenance

Reconstructed. Recorded on 2026-10-17 for code from `5405324` (2026-09-08).

The layering, the `${VAR}` substitution, and the never-create-the-file rule are all directly visible
in `config.py` and stated in `README.md:111-154` and `AGENTS.md`. The reasons those specific
alternatives were rejected were not written down; in particular, the claim that `importlib.resources`
was chosen to survive zip-imported wheels is an inference from a comment at `config.py:45-46`, which
says installed wheels must not depend on the source tree layout but does not mention zip imports.

Two facts in this ADR were established by inspection on 2026-10-17 rather than by reading a record:
that the root `config.default.yaml` had drifted from the packaged copy — it described
`plan.max_iterations` as "legacy ... unused in new pipeline", while `planner.py:657-668` uses it to
bound fix cycles — and that `agent.skills_dir` has no reader. Both were corrected by this backfill;
neither had a recorded decision behind it.
