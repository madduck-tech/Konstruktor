"""Parser for OpenHands JSONL event stream."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


@dataclass
class AgentEvent:
    """A single event from the OpenHands JSONL stream."""
    raw: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class RunSummary:
    """Summary of an agent run parsed from the event stream."""
    run_id: str = ""
    status: str = "unknown"
    exit_code: int | None = None
    agent_exit_code: int | None = None
    task: str = ""
    directory: str = ""
    model: str = ""
    total_steps: int = 0
    files_changed: list[str] = field(default_factory=list)
    error: str | None = None
    final_message: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "agent_exit_code": self.agent_exit_code,
            "task": self.task,
            "directory": self.directory,
            "model": self.model,
            "total_steps": self.total_steps,
            "files_changed": self.files_changed,
            "error": self.error,
            "final_message": self.final_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
        }


def parse_event_line(line: str) -> AgentEvent | None:
    """Parse a single JSONL line into an AgentEvent.

    Returns None for non-JSON or empty lines.
    """
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
        if not isinstance(data, dict):
            return None
        return AgentEvent(raw=data, timestamp=datetime.now(timezone.utc))
    except json.JSONDecodeError:
        return None


def parse_stream(lines: Iterator[str]) -> Iterator[AgentEvent]:
    """Parse an iterator of JSONL lines into AgentEvents."""
    for line in lines:
        event = parse_event_line(line)
        if event is not None:
            yield event


def is_agent_action(event: AgentEvent) -> bool:
    """Return whether an event consumes an agent step."""
    data = event.raw
    kind = data.get("kind", data.get("type", ""))
    return kind in ("ActionEvent", "action") and data.get("source", "agent") == "agent"


def is_finish_event(event: AgentEvent) -> bool:
    """Return whether an event explicitly reports agent completion."""
    data = event.raw
    kind = data.get("kind", data.get("type", ""))
    if str(kind).lower() == "finish":
        return True
    if not is_agent_action(event):
        return False
    action = data.get("action", {})
    if not isinstance(action, dict):
        return False
    action_kind = action.get("kind", action.get("type", ""))
    return action_kind in ("FinishAction", "finish")


def extract_summary(
    events: list[AgentEvent], subprocess_returncode: int | None = None
) -> RunSummary:
    """Extract a RunSummary from a list of parsed events."""
    summary = RunSummary()

    if events:
        summary.started_at = events[0].timestamp
        summary.finished_at = events[-1].timestamp

    last_agent_message = ""
    finish_seen = False
    finish_exit_code: int | None = None

    for evt in events:
        data = evt.raw
        kind = data.get("kind", data.get("type", ""))
        source = data.get("source", "")

        # Count agent actions as steps
        if is_agent_action(evt):
            summary.total_steps += 1

            action = data.get("action", {})
            if isinstance(action, dict):
                action_kind = action.get("kind", action.get("type", ""))
                if action_kind in ("FinishAction", "finish"):
                    finish_seen = True
                    finish_exit_code = action.get("exit_code", 0)
                    last_agent_message = str(action.get("message", ""))
                cmd = action.get("command", "")
                path = action.get("path", "")
                # Track file modifications
                if cmd in ("create", "edit", "str_replace", "write") and path:
                    if path not in summary.files_changed:
                        summary.files_changed.append(path)

        # Track errors from observations
        if kind == "ObservationEvent":
            obs = data.get("observation", {})
            if isinstance(obs, dict) and obs.get("is_error"):
                content = obs.get("content", "")
                if isinstance(content, list):
                    text = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict)
                    )
                elif isinstance(content, str):
                    text = content
                else:
                    text = ""
                if text and not summary.error:
                    summary.error = text[:500]

        # Conversation errors
        if kind == "ConversationErrorEvent":
            detail = data.get("detail", "")
            code = data.get("code", "")
            if detail:
                # Extract user-friendly part from LiteLLM error
                friendly = detail
                # Try to extract the most useful part
                for marker in ["Error from provider", "Upstream request failed", "rate limit"]:
                    if marker.lower() in detail.lower():
                        idx = detail.lower().find(marker.lower())
                        friendly = detail[idx:]
                        break
                if not summary.error:
                    summary.error = friendly[:500]
                else:
                    summary.error += "\n" + friendly[:300]
            elif code:
                if not summary.error:
                    summary.error = code
            summary.status = "error"

        # Agent-level errors (e.g., tool failures)
        if kind == "AgentErrorEvent":
            detail = data.get("detail", "")
            if detail and not summary.error:
                summary.error = detail[:500]
            elif not summary.error:
                summary.error = f"Agent error: {data.get('message', str(data)[:200])}"
            summary.status = "error"

        # Capture final agent message
        if kind == "MessageEvent" and source == "agent":
            msg = data.get("llm_message", {})
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, list):
                    last_agent_message = " ".join(
                        c.get("text", "") for c in content
                        if isinstance(c, dict)
                    )
                elif isinstance(content, str):
                    last_agent_message = content

        # Legacy OpenHands headless output has a dedicated finish event.
        if str(kind).lower() == "finish":
            finish_seen = True
            finish_exit_code = data.get("exit_code", 0)
            last_agent_message = str(data.get("message", last_agent_message))

    # Planner verdicts and compliance matrices commonly appear after a long
    # explanation, so the machine-readable final response must stay intact.
    summary.final_message = last_agent_message

    # Harness codes are stable; preserve implementation-specific agent codes separately.
    if subprocess_returncode not in (None, 0):
        summary.agent_exit_code = subprocess_returncode
        summary.exit_code = 1
        summary.status = "failed"
        if not summary.error:
            summary.error = f"OpenHands exited with code {subprocess_returncode}"
    elif summary.status == "error":
        summary.status = "failed"
        summary.exit_code = 1
    elif finish_seen:
        if not isinstance(finish_exit_code, int):
            finish_exit_code = 1
        summary.agent_exit_code = finish_exit_code
        summary.exit_code = 0 if finish_exit_code == 0 else 1
        summary.status = "success" if finish_exit_code == 0 else "failed"
        if finish_exit_code and not summary.error:
            summary.error = f"Agent finish event reported exit code {finish_exit_code}"
    elif subprocess_returncode == 0 and summary.final_message:
        # OpenHands 1.21 can complete with a final MessageEvent and no
        # dedicated FinishAction. A clean process exit makes that message
        # authoritative, while an empty clean exit remains a failure.
        summary.agent_exit_code = 0
        summary.exit_code = 0
        summary.status = "success"
    elif summary.error:
        summary.status = "failed"
        summary.exit_code = 1
    else:
        summary.status = "failed"
        summary.exit_code = 1
        summary.error = "Agent exited without a completion event"

    return summary
