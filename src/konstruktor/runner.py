"""Core runner — launches OpenHands, monitors progress, handles timeouts."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from konstruktor.config import load_config, validate_config
from konstruktor.logger import get_logger, setup_logging
from konstruktor.parser import (
    AgentEvent,
    RunSummary,
    extract_summary,
    is_agent_action,
    is_finish_event,
    parse_event_line,
)
from konstruktor.reporter import (
    format_json_report,
    format_text_report,
    generate_diff,
    save_summary,
)
from konstruktor.security import validate_runtime_security, validate_working_directory


class RunResult:
    """Result of a single Konstruktor run."""

    def __init__(
        self,
        summary: RunSummary,
        events: list[AgentEvent],
        output_dir: Path,
        diff_path: Optional[Path] = None,
    ):
        self.summary = summary
        self.events = events
        self.output_dir = output_dir
        self.diff_path = diff_path

    @property
    def success(self) -> bool:
        return self.summary.status == "success"

    @property
    def exit_code(self) -> int:
        """Map summary status to exit code."""
        if self.summary.status == "success":
            return 0
        if self.summary.status == "failed":
            return 1
        if self.summary.status == "timeout":
            return 3
        if self.summary.status == "cancelled":
            return 130
        if isinstance(self.summary.exit_code, int) and self.summary.exit_code in (2, 4, 5, 6):
            return self.summary.exit_code
        return 1


class KonstruktorRunner:
    """Runs an autonomous task via OpenHands headless mode."""

    _execution_lock = threading.Lock()

    def __init__(self, config: dict):
        self.config = config
        self.log = get_logger("konstruktor.runner")
        self._process: subprocess.Popen | None = None
        self._cancelled = False
        self._termination_reason: str | None = None

    def run(
        self,
        task: str,
        directory: str,
        model: str | None = None,
        timeout: int | None = None,
        output_dir: str | None = None,
        dry_run: bool = False,
        approve: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> RunResult:
        """Serialize use of this stateful runner instance."""
        with self._execution_lock:
            return self._run(
                task, directory, model, timeout, output_dir, dry_run, approve, extra_env
            )

    def _run(
        self,
        task: str,
        directory: str,
        model: str | None = None,
        timeout: int | None = None,
        output_dir: str | None = None,
        dry_run: bool = False,
        approve: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> RunResult:
        """Execute a task autonomously.

        Args:
            task: Task description.
            directory: Working directory.
            model: Override LLM model.
            timeout: Override timeout in seconds.
            output_dir: Directory for artifacts and logs.
            approve: auto | ask | llm.

        Returns:
            RunResult with summary and events.
        """
        self._process = None
        self._cancelled = False
        self._termination_reason = None

        # Resolve parameters
        workspace = str(Path(directory).expanduser().resolve())
        model = model or self.config["llm"]["model"]
        timeout = timeout or self.config["limits"]["timeout"]
        approve = approve or self.config["agent"]["mode"]
        output_base = Path(output_dir) if output_dir else Path(self.config["logging"]["dir"])
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M%S')
        run_id = f"run-{ts}-{uuid.uuid4().hex[:6]}"
        run_dir = output_base / run_id
        started_at = datetime.now(timezone.utc)

        dir_error = validate_working_directory(directory)
        if dir_error:
            summary = RunSummary(
                run_id=run_id,
                task=task,
                directory=workspace,
                model=str(model),
                status="error",
                exit_code=2,
                error=dir_error,
                started_at=started_at,
            )
            self._save_artifacts(run_dir, summary, [], [], [])
            return RunResult(summary=summary, events=[], output_dir=run_dir)

        api_key = self.config["llm"].get("api_key", "")
        base_url = self.config["llm"].get("base_url") or ""

        if dry_run:
            self._print_dry_run(task, workspace, model, timeout, run_dir, approve)
            finished_at = datetime.now(timezone.utc)
            return RunResult(
                summary=RunSummary(
                    run_id=run_id,
                    task=task,
                    directory=workspace,
                    model=str(model),
                    status="success",
                    exit_code=0,
                    started_at=started_at,
                    finished_at=finished_at,
                ),
                events=[],
                output_dir=run_dir,
            )

        # Setup logging
        setup_logging(
            log_dir=str(run_dir),
            level=self.config["logging"]["level"],
            fmt=self.config["logging"]["format"],
        )
        self.log.info(
            "run_start",
            run_id=run_id,
            task=task[:200],
            directory=workspace,
            model=model,
            timeout=timeout,
        )

        run_dir.mkdir(parents=True, exist_ok=True)

        events: list[AgentEvent] = []
        summary = RunSummary(
            run_id=run_id,
            task=task,
            directory=workspace,
            model=str(model),
            started_at=started_at,
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        try:
            security_error = validate_runtime_security(self.config, approve)
            if security_error:
                summary.status = "error"
                summary.exit_code = 6
                summary.error = security_error
                self._save_artifacts(run_dir, summary, events, stdout_lines, stderr_lines)
                return RunResult(summary=summary, events=events, output_dir=run_dir)

            # Build environment
            env = os.environ.copy()
            env.update(extra_env or {})
            env["LLM_API_KEY"] = api_key
            env["LLM_MODEL"] = str(model)
            if base_url:
                env["LLM_BASE_URL"] = base_url
            sandbox = self.config["agent"].get("sandbox", "docker")
            env["RUNTIME"] = str({"process": "local"}.get(sandbox, sandbox))

            # Build command with approval mode
            cmd = ["openhands", "--headless", "--json", "--override-with-envs"]
            if approve == "auto":
                cmd.append("--always-approve")
            elif approve == "llm":
                cmd.append("--llm-approve")
            # approve == "ask" → no extra flag (default interactive)
            cmd.extend(["--task", task])

            # Change to working directory
            self.log.info("running_openhands", command=" ".join(cmd), cwd=workspace)

            self._process = subprocess.Popen(
                cmd,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=os.name == "posix",
            )

            events, stdout_lines, stderr_lines = self._read_output_with_timeout(timeout)
            returncode = self._process.wait()

            # Extract summary
            summary = extract_summary(events, returncode)
            summary.run_id = run_id
            summary.task = task
            summary.directory = workspace
            summary.model = str(model)
            summary.started_at = started_at
            summary.finished_at = datetime.now(timezone.utc)

            if self._termination_reason in ("timeout", "step_timeout"):
                summary.status = "timeout"
                summary.exit_code = 3
                summary.error = (
                    "Overall timeout exceeded"
                    if self._termination_reason == "timeout"
                    else "Agent step timeout exceeded"
                )
            elif self._termination_reason == "max_steps":
                summary.status = "failed"
                summary.exit_code = 1
                summary.error = "Maximum agent step count exceeded"
            elif self._cancelled:
                summary.status = "cancelled"
                summary.exit_code = 130
                summary.error = "Run cancelled by signal"

            # Generate diff
            diff_path = generate_diff(workspace, run_dir)

            self._save_artifacts(run_dir, summary, events, stdout_lines, stderr_lines)

            # Cleanup old runs
            self._cleanup_old_runs(output_base)

            self.log.info(
                "run_complete",
                run_id=run_id,
                status=summary.status,
                steps=summary.total_steps,
                duration=summary.duration_seconds,
            )

            return RunResult(
                summary=summary,
                events=events,
                output_dir=run_dir,
                diff_path=diff_path,
            )

        except FileNotFoundError:
            self.log.error("openhands_not_found")
            summary.status = "error"
            summary.exit_code = 5
            summary.error = (
                "OpenHands CLI not found.\n"
                "Install with:\n"
                "  uv tool install openhands --python 3.12\n"
                "  or: pip install openhands"
            )
            self._save_artifacts(run_dir, summary, events, stdout_lines, stderr_lines)
            return RunResult(summary=summary, events=events, output_dir=run_dir)

        except Exception as e:
            self.log.exception("run_exception", error=str(e))
            summary.status = "error"
            summary.exit_code = 1
            summary.error = str(e)
            self._kill_process()
            self._save_artifacts(run_dir, summary, events, stdout_lines, stderr_lines)
            return RunResult(summary=summary, events=events, output_dir=run_dir)

    def _read_output_with_timeout(
        self, timeout: int
    ) -> tuple[list[AgentEvent], list[str], list[str]]:
        """Read stdout from subprocess with a timeout.

        Drain both pipes concurrently and parse stdout as events arrive.
        """
        events: list[AgentEvent] = []
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def reader(stream_name: str, stream):
            try:
                for line in stream:
                    output_queue.put((stream_name, line))
            finally:
                output_queue.put((stream_name, None))

        streams = {
            "stdout": self._process.stdout if self._process else None,
            "stderr": self._process.stderr if self._process else None,
        }
        reader_threads = [
            threading.Thread(target=reader, args=(name, stream), daemon=True)
            for name, stream in streams.items()
            if stream is not None
        ]
        for thread in reader_threads:
            thread.start()

        # Setup signal handler for SIGINT/SIGTERM
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        def signal_handler(signum, frame):
            self._cancelled = True
            self._termination_reason = "cancelled"
            self._kill_process()

        can_handle_signals = threading.current_thread() is threading.main_thread()
        if can_handle_signals:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

        try:
            started = time.monotonic()
            last_event = started
            completed_streams = 0
            drain_deadline: float | None = None
            step_timeout = self.config["limits"]["step_timeout"]
            max_steps = self.config["limits"]["max_steps"]
            step_count = 0

            while True:
                now = time.monotonic()
                process_running = self._process is not None and self._process.poll() is None
                if not process_running and completed_streams == len(reader_threads):
                    break
                if drain_deadline is not None and now >= drain_deadline:
                    break
                if self._termination_reason is not None:
                    if drain_deadline is None:
                        drain_deadline = now + 1
                    remaining = min(0.1, drain_deadline - now)
                else:
                    remaining = min(
                        0.1, timeout - (now - started), step_timeout - (now - last_event)
                    )
                if remaining <= 0:
                    if self._termination_reason is None:
                        self._termination_reason = (
                            "timeout" if now - started >= timeout else "step_timeout"
                        )
                        self._kill_process()
                        drain_deadline = time.monotonic() + 1
                    continue
                try:
                    stream_name, line = output_queue.get(timeout=remaining)
                except queue.Empty:
                    continue
                if line is None:
                    completed_streams += 1
                    continue
                if stream_name == "stderr":
                    stderr_lines.append(line)
                    continue

                stdout_lines.append(line)
                event = parse_event_line(line)
                if event is None:
                    continue
                last_event = time.monotonic()
                if is_agent_action(event) and not is_finish_event(event):
                    if step_count >= max_steps and self._termination_reason is None:
                        self._termination_reason = "max_steps"
                        self._kill_process()
                        drain_deadline = time.monotonic() + 1
                        continue
                    step_count += 1
                events.append(event)
        finally:
            if can_handle_signals:
                signal.signal(signal.SIGINT, original_sigint)
                signal.signal(signal.SIGTERM, original_sigterm)
            for thread in reader_threads:
                thread.join(timeout=0.1)

        return events, stdout_lines, stderr_lines

    def _kill_process(self):
        """Kill the OpenHands process and its children."""
        if self._process and self._process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                else:
                    self._process.terminate()
                self._process.wait(timeout=5)
                return
            except (subprocess.TimeoutExpired, OSError, TypeError):
                try:
                    if os.name == "posix":
                        os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
                    else:
                        self._process.kill()
                    self._process.wait(timeout=5)
                    return
                except (subprocess.TimeoutExpired, OSError, TypeError):
                    pass
            try:
                self._process.kill()
            except OSError:
                pass

    def _save_artifacts(
        self,
        run_dir: Path,
        summary: RunSummary,
        events: list[AgentEvent],
        stdout_lines: list[str],
        stderr_lines: list[str],
    ) -> None:
        """Persist a complete diagnostic record, including startup failures."""
        if summary.finished_at is None:
            summary.finished_at = datetime.now(timezone.utc)
        run_dir.mkdir(parents=True, exist_ok=True)
        save_summary(summary, run_dir)
        (run_dir / "events.jsonl").write_text(
            "".join(json.dumps(evt.raw) + "\n" for evt in events), encoding="utf-8"
        )
        (run_dir / "agent.log").write_text("".join(stdout_lines), encoding="utf-8")
        if stderr_lines:
            (run_dir / "agent-error.log").write_text("".join(stderr_lines), encoding="utf-8")

    def _cleanup_old_runs(self, output_base: Path):
        """Remove old run directories exceeding keep_runs limit."""
        if not output_base.exists():
            return
        keep = self.config["logging"].get("keep_runs", 10)
        run_dirs = sorted(
            [d for d in output_base.iterdir() if d.is_dir() and d.name.startswith("run-")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old_dir in run_dirs[keep:]:
            try:
                import shutil
                shutil.rmtree(old_dir)
                self.log.debug("cleaned_old_run", dir=str(old_dir))
            except OSError:
                pass

    def _print_dry_run(
        self,
        task: str,
        workspace: str,
        model: str,
        timeout: int,
        run_dir: Path,
        approve: str,
    ):
        """Print dry-run information."""
        mode_labels = {"auto": "--always-approve", "ask": "(interactive)", "llm": "--llm-approve"}
        mode_label = mode_labels.get(approve, approve)
        print("[DRY RUN] Would execute:", file=sys.stderr)
        print(f"  Run ID:    {run_dir.name}", file=sys.stderr)
        print(f"  Task:      {task}", file=sys.stderr)
        print(f"  Directory: {workspace}", file=sys.stderr)
        print(f"  Model:     {model}", file=sys.stderr)
        print(f"  Timeout:   {timeout}s", file=sys.stderr)
        print(f"  Output:    {run_dir}", file=sys.stderr)
        print(f"  Mode:      {approve} ({mode_label})", file=sys.stderr)


def run_task(
    task: str,
    directory: str = ".",
    model: str | None = None,
    config: str | None = None,
    timeout: int | None = None,
    max_retries: int | None = None,
    output: str | None = None,
    json_output: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    approve: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Top-level function: load config, validate, run task.

    Returns exit code (0 = success, non-zero = failure).
    """
    # Load and validate config
    cfg = load_config(config)

    errors = validate_config(cfg)
    if errors:
        for err in errors:
            print(f"Configuration error: {err}", file=sys.stderr)
        return 4

    if verbose:
        cfg["logging"]["level"] = "debug"

    max_retries = (
        cfg["limits"].get("max_retries", 0) if max_retries is None else max_retries
    )
    approve = approve or cfg["agent"].get("mode", "auto")

    runner = KonstruktorRunner(cfg)

    # Retry loop
    last_result: RunResult | None = None
    max_attempts = 1 + max(0, max_retries)

    for attempt in range(1, max_attempts + 1):
        result = runner.run(
            task=task,
            directory=directory,
            model=model,
            timeout=timeout,
            output_dir=output,
            dry_run=dry_run,
            approve=approve,
            extra_env=extra_env,
        )
        last_result = result

        if result.success or dry_run:
            break

        if result.exit_code in (3, 5, 6, 130):
            break

        if attempt < max_attempts:
            wait = min(2 ** (attempt - 1), 30)
            print(f"Retry {attempt}/{max_retries} in {wait}s...", file=sys.stderr)
            time.sleep(wait)

    if dry_run:
        return 0

    # Output
    if last_result is None:
        return 1

    if json_output:
        print(format_json_report(last_result.summary, cfg))
    else:
        print(format_text_report(last_result.summary, cfg))

    if last_result.output_dir:
        print(f"Output: {last_result.output_dir}", file=sys.stderr)

    return last_result.exit_code
