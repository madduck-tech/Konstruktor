"""Tests for the runner module."""

import copy
import json
import subprocess
import sys
import threading
import time
from unittest.mock import MagicMock, patch

from konstruktor.parser import RunSummary
from konstruktor.runner import KonstruktorRunner, RunResult, run_task

SAMPLE_CONFIG = {
    "llm": {
        "model": "claude-sonnet-4-5-20250929",
        "api_key": "sk-test-key-12345678",
        "base_url": None,
    },
    "limits": {"timeout": 60, "step_timeout": 30, "max_steps": 10, "max_retries": 0},
    "agent": {"mode": "auto", "sandbox": "process", "skills_dir": None},
    "logging": {"dir": "./test-output", "level": "info", "format": "jsonl", "keep_runs": 3},
    "security": {"allowed_commands": [], "blocked_commands": [], "require_approval_for": []},
}


class TestKonstruktorRunner:
    def test_dry_run(self):
        runner = KonstruktorRunner(SAMPLE_CONFIG)
        result = runner.run(
            task="Test task",
            directory="/tmp",
            dry_run=True,
        )
        assert result.success
        assert result.summary.status == "success"

    def test_run_with_output_dir(self, tmp_path):
        runner = KonstruktorRunner(copy.deepcopy(SAMPLE_CONFIG))
        output = tmp_path / "output"

        mock_process = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stdout.__iter__.return_value = iter([])
        mock_process.stderr = MagicMock()
        mock_process.stderr.read.return_value = ""
        mock_process.poll.return_value = 0
        mock_process.wait.return_value = 0

        with patch("subprocess.Popen", return_value=mock_process):
            with patch("konstruktor.runner.generate_diff", return_value=None):
                result = runner.run(
                    task="Test",
                    directory=str(tmp_path),
                    output_dir=str(output),
                )

        assert result.output_dir.exists()
        # Even on error, summary.json should exist
        summary_path = result.output_dir / "summary.json"
        assert summary_path.exists()

    def test_cleanup_old_runs(self, tmp_path):
        config = copy.deepcopy(SAMPLE_CONFIG)
        config["logging"]["dir"] = str(tmp_path)
        config["logging"]["keep_runs"] = 2

        # Create 5 fake run dirs
        for i in range(5):
            d = tmp_path / f"run-2026-01-01-00000{i}"
            d.mkdir()

        runner = KonstruktorRunner(config)
        runner._cleanup_old_runs(tmp_path)

        remaining = [d.name for d in tmp_path.iterdir() if d.is_dir()]
        assert len(remaining) == 2

    def test_kill_process(self):
        runner = KonstruktorRunner(SAMPLE_CONFIG)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.pid = 123
        runner._process = mock_proc
        with patch("konstruktor.runner.os.getpgid", return_value=123):
            with patch("konstruktor.runner.os.killpg") as killpg:
                runner._kill_process()
        killpg.assert_called_once()

    def test_finish_event_and_runtime_determine_result(self, tmp_path):
        config = copy.deepcopy(SAMPLE_CONFIG)
        config["security"]["blocked_commands"] = []
        process = MagicMock()
        process.stdout = iter([json.dumps({"type": "finish", "exit_code": 4}) + "\n"])
        process.stderr = iter([])
        process.wait.return_value = 0
        process.poll.return_value = 0

        with patch("konstruktor.runner.subprocess.Popen", return_value=process) as popen:
            with patch("konstruktor.runner.generate_diff", return_value=None):
                result = KonstruktorRunner(config).run(
                    "test", str(tmp_path), output_dir=str(tmp_path / "out")
                )

        assert result.summary.status == "failed"
        assert result.exit_code == 1
        assert result.summary.agent_exit_code == 4
        assert popen.call_args.kwargs["env"]["RUNTIME"] == "local"
        assert popen.call_args.kwargs["start_new_session"] is True

    def test_missing_openhands_writes_failure_artifacts(self, tmp_path):
        config = copy.deepcopy(SAMPLE_CONFIG)
        with patch("konstruktor.runner.subprocess.Popen", side_effect=FileNotFoundError):
            result = KonstruktorRunner(config).run(
                "test", str(tmp_path), output_dir=str(tmp_path / "out")
            )

        assert result.exit_code == 5
        assert (result.output_dir / "summary.json").exists()
        assert (result.output_dir / "events.jsonl").exists()
        saved = json.loads((result.output_dir / "summary.json").read_text())
        assert saved["exit_code"] == 5

    def test_drains_stderr_while_parsing_stdout(self):
        config = copy.deepcopy(SAMPLE_CONFIG)
        command = (
            "import json, sys; "
            "sys.stderr.write('x' * 200000); sys.stderr.flush(); "
            "print(json.dumps({'type': 'finish', 'exit_code': 0}), flush=True)"
        )
        runner = KonstruktorRunner(config)
        runner._process = subprocess.Popen(
            [sys.executable, "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        events, _, stderr = runner._read_output_with_timeout(5)
        assert runner._process.wait() == 0
        assert events[-1].raw["type"] == "finish"
        assert len("".join(stderr)) == 200000

    def test_step_timeout_kills_process_group(self):
        config = copy.deepcopy(SAMPLE_CONFIG)
        config["limits"]["step_timeout"] = 0.05
        runner = KonstruktorRunner(config)
        runner._process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        started = time.monotonic()
        runner._read_output_with_timeout(5)
        assert time.monotonic() - started < 2
        assert runner._termination_reason == "step_timeout"

    def test_closed_pipes_do_not_bypass_timeout(self):
        config = copy.deepcopy(SAMPLE_CONFIG)
        config["limits"]["step_timeout"] = 0.05
        runner = KonstruktorRunner(config)
        command = "import os, time; os.close(1); os.close(2); time.sleep(10)"
        runner._process = subprocess.Popen(
            [sys.executable, "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )

        started = time.monotonic()
        runner._read_output_with_timeout(5)
        assert time.monotonic() - started < 2
        assert runner._termination_reason == "step_timeout"

    def test_max_steps_allows_finish_at_exact_boundary(self):
        config = copy.deepcopy(SAMPLE_CONFIG)
        config["limits"]["max_steps"] = 2
        lines = [
            json.dumps({"type": "action", "action": {"command": "one"}}) + "\n",
            json.dumps({"type": "action", "action": {"command": "two"}}) + "\n",
            json.dumps({"type": "finish", "exit_code": 0}) + "\n",
        ]
        process = MagicMock()
        process.stdout = iter(lines)
        process.stderr = iter([])
        process.poll.return_value = 0
        runner = KonstruktorRunner(config)
        runner._process = process

        events, _, _ = runner._read_output_with_timeout(5)
        assert runner._termination_reason is None
        assert len(events) == 3

    def test_runner_instance_serializes_runs(self, tmp_path):
        runner = KonstruktorRunner(copy.deepcopy(SAMPLE_CONFIG))
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def fake_run(*args, **kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1
            return RunResult(RunSummary(status="success", exit_code=0), [], tmp_path)

        with patch.object(runner, "_run", side_effect=fake_run):
            threads = [
                threading.Thread(target=runner.run, args=("task", str(tmp_path)))
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        assert max_active == 1

    def test_invalid_directory_returns_argument_error(self, tmp_path):
        runner = KonstruktorRunner(copy.deepcopy(SAMPLE_CONFIG))
        with patch("konstruktor.runner.subprocess.Popen") as popen:
            result = runner.run("task", str(tmp_path / "missing"), output_dir=str(tmp_path))
        assert result.exit_code == 2
        assert result.summary.status == "error"
        popen.assert_not_called()

    def test_extra_environment_is_passed_to_agent(self, tmp_path):
        process = MagicMock()
        process.stdout = iter([json.dumps({"type": "finish", "exit_code": 0}) + "\n"])
        process.stderr = iter([])
        process.poll.return_value = 0
        process.wait.return_value = 0
        runner = KonstruktorRunner(copy.deepcopy(SAMPLE_CONFIG))
        with patch("konstruktor.runner.subprocess.Popen", return_value=process) as popen:
            with patch("konstruktor.runner.generate_diff", return_value=None):
                result = runner.run(
                    "task",
                    str(tmp_path),
                    output_dir=str(tmp_path / "output"),
                    extra_env={"EXTRA_TEST": "yes"},
                )
        assert result.success
        assert popen.call_args.kwargs["env"]["EXTRA_TEST"] == "yes"
        assert result.summary.task == "task"
        assert result.summary.directory == str(tmp_path)
        assert result.summary.model == SAMPLE_CONFIG["llm"]["model"]
        assert result.summary.started_at is not None
        assert result.summary.finished_at is not None


class TestRunTask:
    def test_missing_api_key(self):
        import copy
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["llm"]["api_key"] = ""
        with patch("konstruktor.runner.load_config", return_value=cfg):
            exit_code = run_task(task="test", directory="/tmp")
            assert exit_code == 4  # config error

    def test_dry_run(self):
        with patch("konstruktor.runner.load_config", return_value=SAMPLE_CONFIG):
            exit_code = run_task(task="test", dry_run=True)
            assert exit_code == 0

    def test_uses_retry_and_approval_config_defaults(self, tmp_path):
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["limits"]["max_retries"] = 2
        cfg["agent"]["mode"] = "llm"
        result = RunResult(RunSummary(status="success", exit_code=0), [], tmp_path)
        with patch("konstruktor.runner.load_config", return_value=cfg):
            with patch("konstruktor.runner.KonstruktorRunner.run", return_value=result) as run:
                assert run_task(task="test") == 0
        assert run.call_args.kwargs["approve"] == "llm"
