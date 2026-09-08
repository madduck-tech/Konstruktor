# Konstruktor

![Konstruktor autonomous agent at work](docs/assets/konstruktor-hero.webp)

**Autonomous agent that turns problems into working solutions.**

Konstruktor is a Python CLI wrapper around OpenHands for running coding tasks headlessly.

> [!WARNING]
> Konstruktor is experimental. Its API, configuration, and behavior may change. Review all
> generated changes and use additional isolation before running it on sensitive or production
> systems.

## Requirements

- Python 3.12+
- OpenHands CLI (`openhands`)

## Installation

```bash
uv tool install openhands --python 3.12
uv tool install konstruktor --python 3.12
```

From source:

```bash
git clone <repo>
cd konstruktor
uv pip install -e .
```

## Quick Start

```bash
export KONSTRUKTOR_LLM_API_KEY="sk-..."
konstruktor run -t "Add email validation to the signup form" -d ~/projects/myapp
```

## Commands

### `run`

`run` starts one OpenHands headless session in the selected working directory, waits for it to
finish, captures its events, and records the resulting Git diff.

```bash
konstruktor run -t "Write unit tests for utils.py" -d ./src
konstruktor run -t "Refactor the auth module" --model "gpt-5.5"
konstruktor run -t "Fix all lint failures" --timeout 3600 --max-retries 2
konstruktor run -t "Describe the proposed work" --dry-run
konstruktor run -t "Run tests and fix failures" --json
```

Use `--env KEY=VALUE` to pass additional environment variables to OpenHands. The option can be
repeated.

### `plan`

`plan` runs a spec-driven, multi-session pipeline:

1. Record the intent.
2. Write a specification with numbered requirements, acceptance criteria, and out-of-scope items.
3. Review and, if needed, correct the specification.
4. Write a file-level implementation plan.
5. Implement the specification.
6. Verify every item and build a compliance matrix.
7. Fix only failed items, then re-verify, within the configured limits.
8. Write a final report.

```bash
konstruktor plan -t "Add email validation to the signup form" -d ~/projects/myapp
konstruktor plan -t "Refactor the auth module" --max-fix-cycles 5
konstruktor plan -t "Preview the pipeline" --dry-run
```

The timeout applies to the entire plan pipeline. `max_iterations` limits the initial verification
plus subsequent fix-and-verify attempts; `max_fix_cycles` separately caps fix cycles. The effective
fix count is the lower of those limits.

Plan artifacts are written into the target project:

```text
docs/specs/<task-slug>/
|-- intent.md
|-- spec.md
|-- plan.md
|-- compliance.md
`-- report.md
```

### Run Management

```bash
konstruktor config          # print the merged configuration with the API key masked
konstruktor status          # show the latest run
konstruktor status <run-id> # show one run
konstruktor list            # list completed runs
```

`resume` is present but is not implemented.

## Configuration

Konstruktor loads packaged defaults, then an optional user file, environment overrides, and CLI
flags. It does not create a configuration file automatically.

Create `~/.konstruktor/config.yaml` only when you need to override defaults:

```yaml
llm:
  model: "claude-sonnet-4-5-20250929"
  api_key: "${KONSTRUKTOR_LLM_API_KEY}"
  base_url: null

limits:
  timeout: 1800
  step_timeout: 300
  max_steps: 100
  max_retries: 0

agent:
  mode: "auto"
  sandbox: "process"

plan:
  max_iterations: 3
  max_fix_cycles: 2
  spec_lint_enabled: true

logging:
  dir: "./konstruktor-output"
  keep_runs: 10
```

Supported environment overrides use the `KONSTRUKTOR_*` prefix:

| Variable | Setting |
|---|---|
| `KONSTRUKTOR_LLM_API_KEY` | `llm.api_key` |
| `KONSTRUKTOR_LLM_MODEL` | `llm.model` |
| `KONSTRUKTOR_LLM_BASE_URL` | `llm.base_url` |
| `KONSTRUKTOR_TIMEOUT` | `limits.timeout` |
| `KONSTRUKTOR_LOG_DIR` | `logging.dir` |

Use `--config <path>` to load a different user configuration file.

## Output

Each agent session writes diagnostics under `konstruktor-output` by default:

```text
konstruktor-output/
`-- run-2026-07-02-153000-a1b2c3/
    |-- summary.json
    |-- events.jsonl
    |-- agent.log
    |-- agent-error.log  # present when OpenHands writes to stderr
    |-- diff.patch       # present when Git reports changes
    `-- harness.log
```

Plan mode runs several sessions, so it can create several run directories in addition to the
artifacts under `docs/specs/<task-slug>/`.

## Security

- The default sandbox is `process`, mapped to OpenHands' local runtime. It is not container
  isolation and can access resources available to the current user.
- Headless execution currently supports only `agent.mode: auto`; unsupported approval modes fail
  closed with exit code 6.
- `docker` and `remote` sandbox settings fail closed because this integration cannot verify that
  OpenHands enforces them.
- Non-empty command allowlists, blocklists, or approval rules also fail closed because the current
  OpenHands CLI does not expose a reliable command-policy API.

## Exit Codes

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

## Development

```bash
uv pip install -e ".[dev]"
pytest -v
ruff check src/ tests/
```
