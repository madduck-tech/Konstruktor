"""Security checks — command blocking and approval rules."""

from typing import Optional


def check_blocked_command(command: str, blocked_patterns: list[str]) -> Optional[str]:
    """Check if a command matches any blocked pattern.

    Args:
        command: The shell command to check.
        blocked_patterns: List of forbidden command substrings.

    Returns:
        The matching blocked pattern if found, None otherwise.
    """
    normalized = command.strip()
    for pattern in blocked_patterns:
        if pattern in normalized:
            return pattern
    return None


def requires_approval(command: str, approval_patterns: list[str]) -> Optional[str]:
    """Check if a command requires explicit user approval.

    Args:
        command: The shell command to check.
        approval_patterns: List of patterns that trigger approval.

    Returns:
        The matching pattern if approval is required, None otherwise.
    """
    normalized = command.strip()
    for pattern in approval_patterns:
        if pattern in normalized:
            return pattern
    return None


def validate_working_directory(directory: str) -> Optional[str]:
    """Validate that a working directory exists and is accessible.

    Returns:
        Error message if invalid, None otherwise.
    """
    from pathlib import Path

    path = Path(directory).expanduser().resolve()
    if not path.exists():
        return f"Directory does not exist: {directory}"
    if not path.is_dir():
        return f"Path is not a directory: {directory}"
    if not (path / ".git").exists():
        return None  # just a warning, not a hard error
    return None


def validate_runtime_security(config: dict, _approval_mode: str) -> Optional[str]:
    """Fail closed for controls unsupported by OpenHands headless 1.21."""
    sandbox = config.get("agent", {}).get("sandbox", "process")
    if sandbox != "process":
        return (
            f"OpenHands headless does not enforce the requested {sandbox!r} sandbox. "
            "Use agent.sandbox='process' or a runtime with verified isolation support."
        )
    if _approval_mode != "auto":
        return (
            f"OpenHands headless does not enforce approval mode {_approval_mode!r}. "
            "Use approval mode 'auto' or an interactive runtime."
        )
    security = config.get("security", {})
    has_command_policy = bool(
        security.get("allowed_commands")
        or security.get("blocked_commands")
        or security.get("require_approval_for")
    )
    if has_command_policy:
        return (
            "Configured command or approval rules cannot be enforced by this OpenHands CLI. "
            "Remove the rules or use an OpenHands runtime with command-policy integration."
        )
    return None
