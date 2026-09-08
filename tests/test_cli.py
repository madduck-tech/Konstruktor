"""Tests for the CLI module."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from konstruktor.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestCLIRun:
    def test_run_requires_task(self, runner):
        result = runner.invoke(main, ["run"])
        assert result.exit_code != 0
        assert "task" in result.output.lower() or "error" in result.output.lower()

    def test_dry_run(self, runner):
        with patch("konstruktor.cli.run_task", return_value=0):
            result = runner.invoke(main, ["run", "--task", "test", "--dry-run"])
            assert result.exit_code == 0

    def test_invalid_directory(self, runner):
        result = runner.invoke(main, ["run", "--task", "test", "-d", "/nonexistent/path/xyz"])
        assert result.exit_code != 0

    def test_version(self, runner):
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert result.output.startswith("konstruktor, version ")
        assert "0.1.0" in result.output

    def test_help_uses_public_cli_name(self, runner):
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Usage: konstruktor" in result.output
        assert "Konstruktor" in result.output

    def test_rejects_non_positive_timeout(self, runner):
        result = runner.invoke(main, ["run", "--task", "test", "--timeout", "0"])
        assert result.exit_code == 2

    def test_unspecified_options_defer_to_config(self, runner):
        with patch("konstruktor.cli.run_task", return_value=0) as run_task:
            result = runner.invoke(main, ["run", "--task", "test"])
        assert result.exit_code == 0
        assert run_task.call_args.kwargs["approve"] is None
        assert run_task.call_args.kwargs["max_retries"] is None

    def test_repeatable_env_is_validated_and_propagated(self, runner):
        with patch("konstruktor.cli.run_task", return_value=0) as run_task:
            result = runner.invoke(
                main, ["run", "--task", "test", "-e", "FOO=one", "--env", "BAR=two"]
            )
        assert result.exit_code == 0
        assert run_task.call_args.kwargs["extra_env"] == {"FOO": "one", "BAR": "two"}

    def test_invalid_env_is_rejected(self, runner):
        result = runner.invoke(main, ["run", "--task", "test", "-e", "NOT-A-PAIR"])
        assert result.exit_code == 2


class TestCLIConfig:
    def test_config_info(self, runner):
        with patch("konstruktor.config.load_config", return_value={
            "llm": {"api_key": "sk-1234567890abcdef", "model": "test"},
            "agent": {"mode": "auto"},
            "limits": {"timeout": 100},
            "logging": {"dir": "./out", "level": "info", "format": "jsonl", "keep_runs": 10},
            "security": {
                "allowed_commands": [],
                "blocked_commands": [],
                "require_approval_for": [],
            },
        }):
            result = runner.invoke(main, ["config"])
            assert result.exit_code == 0
            assert "sk-1...cdef" in result.output  # masked key


class TestCLIStatus:
    def test_no_runs(self, runner, tmp_path):
        cfg = {"logging": {"dir": str(tmp_path)}}
        with patch("konstruktor.config.load_config", return_value=cfg):
                result = runner.invoke(main, ["status"])
                assert result.exit_code == 0
                assert "No runs" in result.output


class TestCLIList:
    def test_list_empty(self, runner, tmp_path):
        cfg = {"logging": {"dir": str(tmp_path)}}
        with patch("konstruktor.config.load_config", return_value=cfg):
            result = runner.invoke(main, ["list"])
            assert result.exit_code == 0
            assert "No runs" in result.output


class TestCLIResume:
    def test_resume_not_implemented(self, runner):
        result = runner.invoke(main, ["resume", "--last"])
        assert result.exit_code == 1
        assert "not yet implemented" in result.output
