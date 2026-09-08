"""Tests for the JSONL event parser."""

import json

from konstruktor.parser import (
    AgentEvent,
    RunSummary,
    extract_summary,
    parse_event_line,
    parse_stream,
)


class TestParseEventLine:
    def test_valid_json(self):
        line = json.dumps({"kind": "ActionEvent", "source": "agent", "action": {"command": "write", "path": "test.py"}})
        evt = parse_event_line(line)
        assert evt is not None
        assert evt.raw["kind"] == "ActionEvent"
        assert evt.raw["action"]["path"] == "test.py"

    def test_empty_line(self):
        assert parse_event_line("") is None
        assert parse_event_line("   ") is None

    def test_invalid_json(self):
        assert parse_event_line("not json") is None

    def test_array_json(self):
        line = json.dumps([1, 2, 3])
        assert parse_event_line(line) is None


class TestParseStream:
    def test_parse_multiple(self):
        lines = [
            json.dumps({"kind": "ActionEvent", "source": "agent", "action": {"command": "write", "path": "a.py"}}),
            "",
            json.dumps({"kind": "ObservationEvent", "source": "environment", "observation": {"content": [{"type": "text", "text": "ok"}]}}),
            "garbage",
            json.dumps({"kind": "MessageEvent", "source": "agent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}),
        ]
        events = list(parse_stream(iter(lines)))
        assert len(events) == 3

    def test_empty_stream(self):
        events = list(parse_stream(iter([])))
        assert events == []


class TestExtractSummary:
    def test_successful_run(self):
        events = [
            AgentEvent(raw={"kind": "MessageEvent", "source": "user", "llm_message": {"role": "user", "content": [{"type": "text", "text": "Do X"}]}}),
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"command": "create", "path": "test.py", "file_text": "x"}, "tool_name": "file_editor"}),
            AgentEvent(raw={"kind": "ObservationEvent", "source": "environment", "observation": {"content": [{"type": "text", "text": "File created"}], "is_error": False, "path": "test.py"}}),
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"command": "run", "command_text": "pytest"}, "tool_name": "bash"}),
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"kind": "FinishAction", "message": "All done"}, "tool_name": "finish"}),
            AgentEvent(raw={"kind": "MessageEvent", "source": "agent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "All done"}]}}),
        ]
        summary = extract_summary(events)
        assert summary.status == "success"
        assert summary.total_steps == 3
        assert summary.final_message == "All done"
        assert "test.py" in summary.files_changed

    def test_failed_run_with_error(self):
        events = [
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"command": "run", "command_text": "bad"}, "tool_name": "bash"}),
            AgentEvent(raw={"kind": "ObservationEvent", "source": "environment", "observation": {"content": [{"type": "text", "text": "command not found"}], "is_error": True}}),
            AgentEvent(raw={"kind": "ConversationErrorEvent", "source": "environment", "code": "LLMBadRequestError", "detail": "command not found"}),
        ]
        summary = extract_summary(events)
        assert summary.status == "failed"
        assert summary.total_steps == 1
        assert summary.error is not None

    def test_empty_events(self):
        summary = extract_summary([])
        assert summary.status == "failed"
        assert summary.total_steps == 0
        assert summary.error is not None

    def test_tracks_files_from_edit_actions(self):
        events = [
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"command": "edit", "path": "src/app.py"}, "tool_name": "file_editor"}),
            AgentEvent(raw={"kind": "MessageEvent", "source": "agent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}),
        ]
        summary = extract_summary(events)
        assert "src/app.py" in summary.files_changed

    def test_no_duplicate_files(self):
        events = [
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"command": "write", "path": "f.py"}, "tool_name": "file_editor"}),
            AgentEvent(raw={"kind": "ActionEvent", "source": "agent", "action": {"command": "write", "path": "f.py"}, "tool_name": "file_editor"}),
            AgentEvent(raw={"kind": "MessageEvent", "source": "agent", "llm_message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}),
        ]
        summary = extract_summary(events)
        assert summary.files_changed == ["f.py"]

    def test_legacy_finish_exit_code_controls_status(self):
        events = [AgentEvent(raw={"type": "finish", "exit_code": 7, "message": "nope"})]
        summary = extract_summary(events, subprocess_returncode=0)
        assert summary.status == "failed"
        assert summary.exit_code == 1
        assert summary.agent_exit_code == 7

    def test_nonzero_subprocess_overrides_successful_finish(self):
        events = [AgentEvent(raw={"type": "finish", "exit_code": 0, "message": "done"})]
        summary = extract_summary(events, subprocess_returncode=9)
        assert summary.status == "failed"
        assert summary.exit_code == 1
        assert summary.agent_exit_code == 9

    def test_zero_subprocess_without_finish_fails(self):
        events = [AgentEvent(raw={"kind": "MessageEvent", "source": "agent"})]
        summary = extract_summary(events, subprocess_returncode=0)
        assert summary.status == "failed"
        assert summary.exit_code == 1
        assert summary.error is not None
        assert "completion event" in summary.error

    def test_zero_subprocess_with_final_message_succeeds(self):
        events = [AgentEvent(raw={
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"content": "SPEC REVIEW VERDICT: PASS"},
        })]
        summary = extract_summary(events, subprocess_returncode=0)
        assert summary.status == "success"
        assert summary.exit_code == 0
        assert summary.final_message == "SPEC REVIEW VERDICT: PASS"

    def test_final_message_is_not_truncated(self):
        message = "x" * 600 + "\nVERDICT: PASS"
        events = [AgentEvent(raw={
            "kind": "MessageEvent",
            "source": "agent",
            "llm_message": {"content": message},
        })]
        summary = extract_summary(events, subprocess_returncode=0)
        assert summary.final_message == message

    def test_agent_error_overrides_earlier_message(self):
        events = [
            AgentEvent(raw={
                "kind": "MessageEvent",
                "source": "agent",
                "llm_message": {"content": "Working on it"},
            }),
            AgentEvent(raw={"kind": "AgentErrorEvent", "detail": "tool failed"}),
        ]
        summary = extract_summary(events, subprocess_returncode=0)
        assert summary.status == "failed"
        assert summary.exit_code == 1
        assert summary.error == "tool failed"


class TestRunSummary:
    def test_duration(self):
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=30)
        summary = RunSummary(started_at=t0, finished_at=t1)
        assert summary.duration_seconds == 30.0

    def test_duration_none(self):
        summary = RunSummary()
        assert summary.duration_seconds is None

    def test_to_dict(self):
        from datetime import datetime, timezone
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        summary = RunSummary(
            run_id="test-1",
            task="Do it",
            directory="/work",
            model="test-model",
            status="success",
            exit_code=0,
            total_steps=5,
            files_changed=["a.py"],
            started_at=t0,
            finished_at=t0,
        )
        d = summary.to_dict()
        assert d["run_id"] == "test-1"
        assert d["status"] == "success"
        assert d["total_steps"] == 5
        assert d["task"] == "Do it"
        assert d["directory"] == "/work"
        assert d["model"] == "test-model"
        assert d["duration_seconds"] == 0.0
