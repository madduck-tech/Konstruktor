"""Reporter — formats and outputs run results."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from konstruktor.parser import RunSummary


def save_summary(summary: RunSummary, output_dir: Path) -> Path:
    """Save summary.json to the output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "summary.json"
    with open(path, "w") as f:
        json.dump(summary.to_dict(), f, indent=2, default=str)
    return path


def generate_diff(working_dir: str, output_dir: Path) -> Path | None:
    """Generate a git diff patch and save to output directory.

    Returns the path to the diff file, or None if git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color", "HEAD", "--"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        patch = result.stdout
        output_path = output_dir.resolve()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        for relative_path in filter(None, untracked.stdout.split("\0")):
            candidate = (Path(working_dir) / relative_path).resolve()
            if candidate == output_path or candidate.is_relative_to(output_path):
                continue
            file_diff = subprocess.run(
                ["git", "diff", "--no-index", "--no-color", "--", "/dev/null", relative_path],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if file_diff.returncode in (0, 1):
                patch += file_diff.stdout
        if patch.strip():
            output_dir.mkdir(parents=True, exist_ok=True)
            diff_path = output_dir / "diff.patch"
            diff_path.write_text(patch, encoding="utf-8")
            return diff_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def format_text_report(summary: RunSummary, config: dict) -> str:
    """Format a human-readable text report."""
    lines = [
        "=" * 60,
        "  Konstruktor Run Report",
        "=" * 60,
        "",
        f"  Status:       {summary.status.upper()}",
        f"  Exit code:    {summary.exit_code}",
        f"  Steps:        {summary.total_steps}",
        f"  Duration:     {summary.duration_seconds:.1f}s"
        if summary.duration_seconds
        else "  Duration:     N/A",
        f"  Files changed: {len(summary.files_changed)}",
    ]

    if summary.files_changed:
        lines.append("")
        lines.append("  Changed files:")
        for f in summary.files_changed:
            lines.append(f"    - {f}")

    if summary.error:
        lines.append("")
        lines.append(f"  Error: {summary.error}")

    if summary.final_message:
        lines.append("")
        lines.append(f"  Agent: {summary.final_message}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def format_json_report(summary: RunSummary, config: dict) -> str:
    """Format a JSON report string."""
    return json.dumps(summary.to_dict(), indent=2, default=str)
