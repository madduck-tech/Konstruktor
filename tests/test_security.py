"""Tests for the security module."""

from konstruktor.security import (
    check_blocked_command,
    requires_approval,
    validate_runtime_security,
    validate_working_directory,
)


class TestCheckBlockedCommand:
    def test_blocked(self):
        assert check_blocked_command("rm -rf /", ["rm -rf /"]) == "rm -rf /"

    def test_not_blocked(self):
        assert check_blocked_command("ls -la", ["rm -rf /"]) is None

    def test_substring_match(self):
        blocked = ["sudo rm -rf"]
        assert check_blocked_command("sudo rm -rf --no-preserve-root /", blocked) == "sudo rm -rf"

    def test_empty_command(self):
        assert check_blocked_command("", ["danger"]) is None


class TestRequiresApproval:
    def test_needs_approval(self):
        approvals = ["git push --force"]
        assert requires_approval("git push --force origin main", approvals) == "git push --force"

    def test_no_approval_needed(self):
        approvals = ["git push --force"]
        assert requires_approval("git push origin main", approvals) is None

    def test_multiple_patterns(self):
        approvals = ["git push --force", "git push --delete"]
        cmd = "git push --delete origin branch"
        assert requires_approval(cmd, approvals) == "git push --delete"


class TestValidateWorkingDirectory:
    def test_existing_dir(self, tmp_path):
        assert validate_working_directory(str(tmp_path)) is None

    def test_nonexistent(self, tmp_path):
        assert validate_working_directory(str(tmp_path / "nonexistent")) is not None

    def test_file_not_dir(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello")
        error = validate_working_directory(str(f))
        assert error is not None
        assert "not a directory" in error.lower()


def test_process_runtime_command_rules_fail_closed():
    config = {
        "agent": {"sandbox": "process"},
        "security": {"blocked_commands": ["sudo"], "allowed_commands": []},
    }
    assert validate_runtime_security(config, "auto") is not None


def test_unsupported_runtime_and_approval_fail_closed():
    config = {
        "agent": {"sandbox": "docker"},
        "security": {"blocked_commands": [], "allowed_commands": []},
    }
    assert validate_runtime_security(config, "auto") is not None
    config["agent"]["sandbox"] = "process"
    assert validate_runtime_security(config, "llm") is not None


def test_docker_runtime_command_rules_also_fail_closed():
    config = {
        "agent": {"sandbox": "docker"},
        "security": {"blocked_commands": ["sudo"], "allowed_commands": []},
    }
    assert validate_runtime_security(config, "auto") is not None
