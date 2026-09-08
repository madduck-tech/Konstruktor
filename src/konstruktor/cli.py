"""CLI entry point using Click."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from konstruktor import __version__
from konstruktor.runner import run_task


def _parse_env(
    _ctx: click.Context, _param: click.Parameter, values: tuple[str, ...]
) -> dict[str, str]:
    """Validate repeatable KEY=VALUE environment overrides."""
    result = {}
    for value in values:
        key, separator, env_value = value.partition("=")
        if (
            not separator
            or not key
            or not key.isascii()
            or not key.replace("_", "a").isalnum()
            or key[0].isdigit()
        ):
            raise click.BadParameter(f"must be KEY=VALUE with a valid environment name: {value!r}")
        result[key] = env_value
    return result


@click.group(name="konstruktor", context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
def main():
    """Konstruktor: autonomous agent that turns problems into working solutions.

    Runs OpenHands autonomously: give it a task and a directory,
    it solves the problem without asking questions.
    """


@main.command()
@click.option("-t", "--task", required=True, help="Task description in natural language.")
@click.option(
    "-d", "--directory",
    default=".",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Working directory (default: current).",
)
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to config file (default: ~/.konstruktor/config.yaml).",
)
@click.option("-m", "--model", help="Override LLM model (e.g. claude-sonnet-4-5-20250929).")
@click.option(
    "--timeout", type=click.IntRange(min=1), help="Timeout in seconds (default from config)."
)
@click.option(
    "--max-retries",
    type=click.IntRange(min=0),
    default=None,
    help="Number of retry attempts on failure (default from config).",
)
@click.option(
    "-o", "--output",
    type=click.Path(resolve_path=True),
    help="Output directory for logs and artifacts.",
)
@click.option("--json", "json_output", is_flag=True, help="Output result as JSON.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose/debug output.")
@click.option(
    "--approve",
    type=click.Choice(["auto", "ask", "llm"]),
    default=None,
    help="Approval mode: auto, ask, llm (default from config).",
)
@click.option(
    "-e",
    "--env",
    "extra_env",
    multiple=True,
    callback=_parse_env,
    help="Additional environment variable as KEY=VALUE (repeatable).",
)
def run(
    task: str,
    directory: str,
    config: str | None,
    model: str | None,
    timeout: int | None,
    max_retries: int | None,
    output: str | None,
    json_output: bool,
    dry_run: bool,
    verbose: bool,
    approve: str | None,
    extra_env: dict[str, str],
):
    """Execute a task autonomously.

    Examples:

        konstruktor run -t "Add unit tests for auth.py"

        konstruktor run -t "Fix all linter errors" -d ~/projects/webapp

        konstruktor run -t "Refactor the auth module" --timeout 3600 --max-retries 2

        konstruktor run -t "Write a README" --json | jq .
    """
    exit_code = run_task(
        task=task,
        directory=directory,
        model=model,
        config=config,
        timeout=timeout,
        max_retries=max_retries,
        output=output,
        json_output=json_output,
        dry_run=dry_run,
        verbose=verbose,
        approve=approve,
        extra_env=extra_env,
    )
    sys.exit(exit_code)


@main.command()
@click.option("-t", "--task", required=True, help="Task description in natural language.")
@click.option(
    "-d", "--directory",
    default=".",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    help="Working directory (default: current).",
)
@click.option(
    "-c", "--config",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to config file.",
)
@click.option("-m", "--model", help="Override LLM model.")
@click.option("--timeout", type=click.IntRange(min=1), help="Timeout in seconds per step.")
@click.option(
    "--max-iterations",
    type=click.IntRange(min=1),
    default=None,
    help="Max execute→verify loops (default from config).",
)
@click.option(
    "--max-fix-cycles",
    type=click.IntRange(min=0),
    default=None,
    help="Max fix-only cycles after first verification fails (default from config).",
)
@click.option(
    "-o", "--output",
    type=click.Path(resolve_path=True),
    help="Output directory for logs and artifacts.",
)
@click.option("--json", "json_output", is_flag=True, help="Output result as JSON.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing.")
@click.option("-v", "--verbose", is_flag=True, help="Verbose/debug output.")
@click.option(
    "--approve",
    type=click.Choice(["auto", "ask", "llm"]),
    default=None,
    help="Approval mode: auto, ask, llm (default from config).",
)
def plan(
    task: str,
    directory: str,
    config: str | None,
    model: str | None,
    timeout: int | None,
    max_iterations: int | None,
    max_fix_cycles: int | None,
    output: str | None,
    json_output: bool,
    dry_run: bool,
    verbose: bool,
    approve: str | None,
):
    """Intent → Spec → Review → Plan → Implement → Verify → Fix → Report.

    Spec-driven development pipeline:
    Step 0: Intent — short goal statement → docs/specs/<slug>/intent.md
    Step 1: Specification — numbered R1..Rn, A1..An, O1..On → spec.md
    Step 2: Review — lint spec against quality checklist, rewrite if needed
    Step 3: Plan — concrete file-level implementation plan → plan.md
    Step 4: Implement — minimal implementation, only what spec requires
    Step 5: Verify — compliance matrix with evidence per requirement → compliance.md
    Step 6: Fix — targeted fixes for failed requirements (up to 2 cycles)
    Step 7: Report — summary, files changed, compliance, risks → report.md

    Examples:

        konstruktor plan -t "Add email validation to signup form"

        konstruktor plan -t "Refactor auth module" -d ~/projects/backend --max-fix-cycles 5
    """
    from konstruktor.config import load_config
    from konstruktor.planner import run_plan

    approve = approve or load_config(config)["agent"]["mode"]

    exit_code = run_plan(
        task=task,
        directory=directory,
        model=model,
        config=config,
        timeout=timeout,
        max_iterations=max_iterations,
        max_fix_cycles=max_fix_cycles,
        output=output,
        json_output=json_output,
        dry_run=dry_run,
        verbose=verbose,
        approve=approve,
    )
    sys.exit(exit_code)


@main.command("config")
@click.option(
    "-c", "--config",
    type=click.Path(resolve_path=True),
    help="Path to config file.",
)
def config_info(config: str | None):
    """Show current configuration."""
    import json

    from konstruktor.config import load_config

    cfg = load_config(config)
    # Mask API key
    if cfg.get("llm", {}).get("api_key"):
        key = cfg["llm"]["api_key"]
        if len(key) > 8:
            cfg["llm"]["api_key"] = key[:4] + "..." + key[-4:]

    print(json.dumps(cfg, indent=2, default=str))


@main.command()
@click.argument("run_id", required=False)
def status(run_id: str | None):
    """Show status of the last run or a specific run by ID."""
    import json

    from konstruktor.config import load_config

    cfg = load_config()
    output_base = Path(cfg["logging"]["dir"])

    if not output_base.exists():
        print("No runs found.", file=sys.stderr)
        sys.exit(0)

    run_dirs = sorted(
        [d for d in output_base.iterdir() if d.is_dir() and d.name.startswith("run-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if run_id:
        run_dirs = [d for d in run_dirs if d.name == run_id]

    if not run_dirs:
        print(f"No runs found{f' matching {run_id}' if run_id else ''}.", file=sys.stderr)
        sys.exit(0)

    for rd in run_dirs[:1]:
        summary_path = rd / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            print(f"Run: {rd.name}")
            print(f"  Status:  {summary.get('status', 'unknown')}")
            print(f"  Steps:   {summary.get('total_steps', '?')}")
            print(f"  Duration: {summary.get('duration_seconds', '?')}s")
            if summary.get("files_changed"):
                print(f"  Files:   {', '.join(summary['files_changed'])}")
        else:
            print(f"Run: {rd.name} (no summary — may be incomplete)")


@main.command()
@click.option("--last", is_flag=True, help="Resume the most recent run.")
@click.argument("run_id", required=False)
def resume(run_id: str | None, last: bool):
    """Resume a previous run. Not yet implemented."""
    print("Resume is not yet implemented.", file=sys.stderr)
    print("Use: openhands --resume <id>", file=sys.stderr)
    sys.exit(1)


@main.command("list")
def list_runs():
    """List all completed runs."""
    import json

    from konstruktor.config import load_config

    cfg = load_config()
    output_base = Path(cfg["logging"]["dir"])

    if not output_base.exists():
        print("No runs found.", file=sys.stderr)
        return

    run_dirs = sorted(
        [d for d in output_base.iterdir() if d.is_dir() and d.name.startswith("run-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not run_dirs:
        print("No runs found.", file=sys.stderr)
        return

    print(f"{'RUN ID':<40} {'STATUS':<10} {'STEPS':<8} {'DURATION':<10}")
    print("-" * 68)
    for rd in run_dirs:
        summary_path = rd / "summary.json"
        if summary_path.exists():
            s = json.loads(summary_path.read_text())
            print(
                f"{rd.name:<40} "
                f"{s.get('status', '?'):<10} "
                f"{str(s.get('total_steps', '?')):<8} "
                f"{str(s.get('duration_seconds', '?')):<10}"
            )
        else:
            print(f"{rd.name:<40} {'pending':<10}")


if __name__ == "__main__":
    main()
