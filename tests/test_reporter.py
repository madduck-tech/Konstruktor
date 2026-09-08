"""Tests for the reporter module."""

import json

from konstruktor.parser import RunSummary
from konstruktor.reporter import (
    format_json_report,
    format_text_report,
    generate_diff,
    save_summary,
)


class TestSaveSummary:
    def test_save(self, tmp_path):
        summary = RunSummary(
            run_id="test-1",
            status="success",
            exit_code=0,
            total_steps=3,
        )
        path = save_summary(summary, tmp_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["status"] == "success"
        assert data["run_id"] == "test-1"


class TestFormatTextReport:
    def test_success_report(self):
        summary = RunSummary(
            run_id="run-1",
            status="success",
            exit_code=0,
            total_steps=5,
            files_changed=["a.py", "b.py"],
            final_message="All good",
        )
        report = format_text_report(summary, {})
        assert "SUCCESS" in report
        assert "a.py" in report
        assert "b.py" in report
        assert "All good" in report

    def test_error_report(self):
        summary = RunSummary(
            run_id="run-1",
            status="error",
            exit_code=1,
            error="Something went wrong",
        )
        report = format_text_report(summary, {})
        assert "ERROR" in report
        assert "Something went wrong" in report


class TestFormatJsonReport:
    def test_json_output(self):
        summary = RunSummary(
            run_id="r1",
            status="success",
            exit_code=0,
            total_steps=2,
        )
        output = format_json_report(summary, {})
        data = json.loads(output)
        assert data["run_id"] == "r1"
        assert data["status"] == "success"
        assert data["total_steps"] == 2


def test_generate_diff_includes_staged_and_untracked(tmp_path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "base",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    tracked.write_text("after\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    (tmp_path / "new.txt").write_text("new\n")

    path = generate_diff(str(tmp_path), tmp_path / "output")

    assert path is not None
    patch = path.read_text()
    assert "tracked.txt" in patch
    assert "new.txt" in patch
