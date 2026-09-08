"""Tests for the planner module — new spec-driven pipeline."""

import re
from pathlib import Path
from unittest.mock import patch

from konstruktor.parser import RunSummary
from konstruktor.planner import (
    ComplianceItem,
    Planner,
    PlanResult,
    _expected_compliance_ids,
    _parse_compliance_matrix,
    _parse_review_verdict,
    _parse_verdict,
    _slugify,
    run_plan,
)
from konstruktor.runner import RunResult

SAMPLE_CONFIG = {
    "llm": {
        "model": "openai/deepseek-v4-pro",
        "api_key": "sk-test-key-12345678",
        "base_url": "https://opencode.ai/zen/go/v1",
    },
    "limits": {"timeout": 60, "step_timeout": 30, "max_steps": 10, "max_retries": 0},
    "agent": {"mode": "auto", "sandbox": "process", "skills_dir": None},
    "logging": {"dir": "./test-output", "level": "info", "format": "jsonl", "keep_runs": 3},
    "security": {"allowed_commands": [], "blocked_commands": [], "require_approval_for": []},
    "plan": {"max_fix_cycles": 2, "spec_lint_enabled": True},
}

SPEC_R1 = """# Specification

## Functional Requirements
R1. Implement the feature

## Acceptance Criteria
A1. Tests pass

## Out of Scope
O1. Do not change unrelated code
"""
SPEC_R1_A1 = SPEC_R1
SPEC_R1_R2 = SPEC_R1.replace(
    "R1. Implement the feature", "R1. Implement the feature\nR2. Add tests"
)


# ══════════════════════════════════════════════════════════════
# Helpers for tests
# ══════════════════════════════════════════════════════════════

def _make_success_result(message: str = "Done") -> RunResult:
    summary = RunSummary(status="success", total_steps=1, final_message=message)
    return RunResult(summary=summary, events=[], output_dir=Path("/tmp"))


def _make_error_result(
    error: str = "Something went wrong", exit_code: int | None = None
) -> RunResult:
    summary = RunSummary(
        status="error", total_steps=0, error=error, exit_code=exit_code
    )
    return RunResult(summary=summary, events=[], output_dir=Path("/tmp"))


def _write_prompt_artifact(task: str, spec: str = SPEC_R1) -> None:
    """Emulate the agent's required file writes for pipeline tests."""
    patterns = (
        ("short intent document", r"to `([^`]+)`", "# Intent\n\nGoal\n"),
        ("detailed implementation specification", r"to `([^`]+)`", spec),
        ("implementation plan based", r"to `([^`]+)`", "# Implementation Plan\n\n1. Edit file\n"),
        ("writing a final report", r"to `([^`]+)`", "## Summary\n\nComplete.\n"),
    )
    for marker, pattern, content in patterns:
        if marker in task:
            match = re.search(pattern, task)
            assert match is not None
            Path(match.group(1)).write_text(content)
            return


# ══════════════════════════════════════════════════════════════
# Slugify
# ══════════════════════════════════════════════════════════════


class TestSlugify:
    def test_basic(self):
        assert _slugify("Add email validation") == "add-email-validation"

    def test_special_chars(self):
        assert _slugify("Fix bug: login fails!") == "fix-bug-login-fails"

    def test_long_task(self):
        long_task = "Implement a very long task description that goes on and on " * 5
        slug = _slugify(long_task)
        assert len(slug) <= 80

    def test_russian(self):
        slug = _slugify("Добавить валидацию email")
        assert "email" in slug


# ══════════════════════════════════════════════════════════════
# Parse verdict (now returns tuple)
# ══════════════════════════════════════════════════════════════


class TestParseVerdict:
    def test_verdict_pass(self):
        output = "All checks done.\nVERDICT: PASS\nEverything is good."
        passed, issues = _parse_verdict(output)
        assert passed is True
        assert issues == ""

    def test_verdict_fail(self):
        output = "Some issues found.\nVERDICT: FAIL — missing tests, bad formatting\n"
        passed, issues = _parse_verdict(output)
        assert passed is False
        assert "missing tests" in issues

    def test_unanchored_heuristic_cannot_pass(self):
        output = "All criteria are met. Everything passes. Good job."
        passed, issues = _parse_verdict(output)
        assert passed is False

    def test_heuristic_fail_with_good_words(self):
        output = "All good but issues remain: missing file x.py"
        passed, issues = _parse_verdict(output)
        # fallback: no PASS marker, and heuristic detects "issues remain"
        assert passed is False

    def test_empty_output(self):
        passed, issues = _parse_verdict("")
        assert passed is False

    def test_contradictory_verdicts_fail(self):
        passed, issues = _parse_verdict("VERDICT: PASS\nVERDICT: FAIL — broken")
        assert passed is False
        assert "Contradictory" in issues

    def test_pass_from_matrix_all_done(self):
        output = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | Add endpoint | ✅ Done | controller.py:42 |\n"
            "| A1 | 200 response | ✅ Done | test_api.py:15 |\n"
            "VERDICT: PASS\n"
        )
        passed, issues = _parse_verdict(output)
        assert passed is True

    def test_fail_from_matrix_not_done(self):
        output = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | Add endpoint | ✅ Done | controller.py:42 |\n"
            "| R2 | Add validation | ❌ Not done | missing |\n"
        )
        passed, issues = _parse_verdict(output)
        assert passed is False


# ══════════════════════════════════════════════════════════════
# Parse compliance matrix
# ══════════════════════════════════════════════════════════════


class TestParseComplianceMatrix:
    def test_parse_simple(self):
        text = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | Add endpoint | ✅ Done | controller.py |\n"
            "| R2 | Add validation | ❌ Not done | — |\n"
        )
        matrix = _parse_compliance_matrix(text)
        assert len(matrix.items) == 2
        assert matrix.items[0].id == "R1"
        assert matrix.items[0].is_done is True
        assert matrix.items[1].id == "R2"
        assert matrix.items[1].is_done is False

    def test_parse_with_acceptance_criteria(self):
        text = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| A1 | Return 200  | ✅ Done | test:test_get_user |\n"
            "| A2 | Return 404  | ❌ Not done | test missing |\n"
            "| O1 | No UI changes | ✅ Done | no UI files touched |\n"
        )
        matrix = _parse_compliance_matrix(text)
        assert len(matrix.items) == 3
        assert matrix.items[0].id == "A1"
        assert matrix.items[2].id == "O1"

    def test_all_done(self):
        text = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | X | ✅ Done | x |\n"
            "| R2 | Y | ✅ Done | y |\n"
        )
        matrix = _parse_compliance_matrix(text)
        assert matrix.all_done is True
        assert matrix.failed_ids == []

    def test_some_failed(self):
        text = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | X | ✅ Done | x |\n"
            "| R2 | Y | ❌ Not done | y |\n"
            "| R3 | Z | ⚠️ Partially done | z |\n"
        )
        matrix = _parse_compliance_matrix(text)
        assert matrix.all_done is False
        assert matrix.failed_ids == ["R2", "R3"]

    def test_empty_input(self):
        matrix = _parse_compliance_matrix("No table here.")
        assert len(matrix.items) == 0
        assert matrix.all_done is False

    def test_malformed_status_and_blank_evidence_do_not_pass(self):
        text = (
            "| R1 | X | passed | file.py |\n"
            "| R2 | Y | ✅ Done |   |\n"
        )
        matrix = _parse_compliance_matrix(text)
        assert matrix.all_done is False
        assert matrix.failed_ids == ["R1", "R2"]

    def test_failed_descriptions(self):
        text = (
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | Add tests | ❌ Not done | missing |\n"
        )
        matrix = _parse_compliance_matrix(text)
        desc = matrix.failed_descriptions
        assert "R1" in desc
        assert "Add tests" in desc


# ══════════════════════════════════════════════════════════════
# Parse review verdict
# ══════════════════════════════════════════════════════════════


class TestParseReviewVerdict:
    def test_pass(self):
        passed, issues = _parse_review_verdict("SPEC REVIEW VERDICT: PASS")
        assert passed is True
        assert issues == ""

    def test_fail(self):
        text = "SPEC REVIEW VERDICT: FAIL — R1 is too broad, A2 has no criterion"
        passed, issues = _parse_review_verdict(text)
        assert passed is False
        assert "R1 is too broad" in issues

    def test_empty(self):
        passed, issues = _parse_review_verdict("")
        assert passed is False

    def test_contradictory_verdicts_fail(self):
        text = "SPEC REVIEW VERDICT: PASS\nSPEC REVIEW VERDICT: FAIL — broad"
        passed, issues = _parse_review_verdict(text)
        assert passed is False
        assert "Contradictory" in issues


# ══════════════════════════════════════════════════════════════
# Specification structure
# ══════════════════════════════════════════════════════════════


class TestExpectedComplianceIds:
    def test_requires_r_a_and_o_sections_and_ids(self):
        ids, error = _expected_compliance_ids(SPEC_R1)
        assert ids == ["R1", "A1", "O1"]
        assert error is None

        for section in (
            "## Functional Requirements\nR1. Implement the feature\n\n",
            "## Acceptance Criteria\nA1. Tests pass\n\n",
            "## Out of Scope\nO1. Do not change unrelated code\n",
        ):
            ids, error = _expected_compliance_ids(SPEC_R1.replace(section, ""))
            assert ids == []
            assert error is not None

    def test_section_without_matching_id_fails(self):
        spec = SPEC_R1.replace("A1. Tests pass", "No numbered criterion")
        ids, error = _expected_compliance_ids(spec)
        assert ids == []
        assert error == "Specification is missing required IDs: A"


# ══════════════════════════════════════════════════════════════
# Compliance data structures
# ══════════════════════════════════════════════════════════════


class TestComplianceItem:
    def test_only_exact_done_status_with_evidence_passes(self):
        assert ComplianceItem("R1", "x", "✅ Done", "x.py").is_done
        assert not ComplianceItem("R1", "x", "Done", "x.py").is_done
        assert not ComplianceItem("R1", "x", "PASS", "x.py").is_done
        assert not ComplianceItem("R1", "x", "passed", "x.py").is_done
        assert not ComplianceItem("R1", "x", "✅ Done", "").is_done

    def test_not_done_statuses(self):
        assert not ComplianceItem("R1", "x", "❌ Not done").is_done
        assert not ComplianceItem("R1", "x", "Not done").is_done
        assert not ComplianceItem("R1", "x", "missing").is_done
        assert not ComplianceItem("R1", "x", "").is_done


# ══════════════════════════════════════════════════════════════
# Planner — integration tests
# ══════════════════════════════════════════════════════════════


class TestPlanner:
    def test_dry_run(self):
        """Dry run should produce spec_dir path without executing."""
        planner = Planner(SAMPLE_CONFIG)
        result = planner.run(
            task="Add email validation",
            directory="/tmp",
            dry_run=True,
        )
        assert result.spec_dir is not None
        assert result.spec_path is not None

    def test_spec_not_created_stops_early(self, tmp_path):
        """If spec file doesn't exist after spec step, should return error."""
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            task = kwargs["task"]
            if "short intent document" in task:
                _write_prompt_artifact(task)
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.error is not None
        assert "not created" in result.error.lower()

    def test_intent_not_created_stops_early(self, tmp_path):
        """If intent file doesn't exist, should return error."""
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.error is not None

    def test_failed_step_stops_even_if_artifact_was_written(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            _write_prompt_artifact(kwargs["task"])
            return _make_error_result("agent failed")

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.error == "Intent step failed: agent failed"
        assert len(result.results) == 1

    def test_identical_intent_rewrite_is_fresh(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        intent = tmp_path / "docs" / "specs" / "task" / "intent.md"
        intent.parent.mkdir(parents=True)
        intent.write_text("# Intent\n\nGoal\n")
        tasks = []

        def fake_run(**kwargs):
            task = kwargs["task"]
            tasks.append(task)
            _write_prompt_artifact(task)
            if "detailed implementation specification" in task:
                return _make_error_result("stop after freshness check")
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert len(tasks) == 2
        assert result.error == "Spec step failed: stop after freshness check"

    def test_untouched_stale_intent_does_not_satisfy_producer(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        intent = tmp_path / "docs" / "specs" / "task" / "intent.md"
        intent.parent.mkdir(parents=True)
        intent.write_text("# Intent\n\nGoal\n")

        with patch.object(planner.runner, "run", return_value=_make_success_result()):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert "did not create or update" in (result.error or "")

    def test_full_pipeline_passes(self, tmp_path):
        """Happy path: all steps succeed, verify passes immediately."""
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            task = kwargs.get("task", "")
            _write_prompt_artifact(task, SPEC_R1_A1)
            msg = ""
            if "SPEC REVIEW VERDICT" in task:
                msg = "SPEC REVIEW VERDICT: PASS"
            elif "VERDICT" in task and "compliance" in task.lower():
                # The verify step uses _VERIFY_PROMPT
                msg = (
                    "| ID | Requirement | Status | Evidence |\n"
                    "|----|-------------|--------|----------|\n"
                    "| R1 | Add feature | ✅ Done | src/app.py:42 |\n"
                    "| A1 | Tests pass  | ✅ Done | pytest output |\n"
                    "| O1 | No unrelated changes | ✅ Done | git diff |\n"
                    "VERDICT: PASS\n"
                )
            return _make_success_result(msg)

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Add email validation", directory=str(tmp_path))

        assert result.passed is True
        assert result.iterations == 1
        assert result.fix_cycles == 0
        assert result.report_content
        assert result.results[-1].success

    def test_existing_identical_compliance_does_not_cause_freshness_failure(
        self, tmp_path
    ):
        planner = Planner(SAMPLE_CONFIG)
        compliance_path = tmp_path / "docs" / "specs" / "task" / "compliance.md"
        compliance_path.parent.mkdir(parents=True)
        compliance_path.write_text(
            "# Compliance Matrix\n\n"
            "| ID | Requirement | Status | Evidence |\n"
            "|----|-------------|--------|----------|\n"
            "| R1 | Feature | ✅ Done | file.py |\n"
            "| A1 | Tests | ✅ Done | pytest |\n"
            "| O1 | Scope | ✅ Done | git diff |\n"
        )

        def fake_run(**kwargs):
            task = kwargs["task"]
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: PASS"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.passed is True
        assert result.error is None

    def test_incomplete_matrix_without_explicit_failures_skips_fix_loop(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        tasks = []

        def fake_run(**kwargs):
            task = kwargs["task"]
            tasks.append(task)
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\nVERDICT: FAIL — incomplete"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.passed is False
        assert result.fix_cycles == 0
        assert not any("fixing specific failed requirements" in task for task in tasks)

    def test_malformed_verdict_without_explicit_failures_skips_fix_loop(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        tasks = []

        def fake_run(**kwargs):
            task = kwargs["task"]
            tasks.append(task)
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "Everything passed"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.passed is False
        assert result.fix_cycles == 0
        assert not any("fixing specific failed requirements" in task for task in tasks)

    def test_malformed_matrix_status_skips_fix_loop(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        tasks = []

        def fake_run(**kwargs):
            task = kwargs["task"]
            tasks.append(task)
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | failed | missing |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: FAIL — malformed status"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.fix_cycles == 0
        assert not any("fixing specific failed requirements" in task for task in tasks)

    def test_pass_requires_exact_spec_coverage(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            task = kwargs["task"]
            _write_prompt_artifact(task, SPEC_R1_A1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "VERDICT: PASS"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(
                task="Task", directory=str(tmp_path), max_fix_cycles=0
            )

        assert result.passed is False
        assert result.report_content

    def test_report_must_be_created(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            task = kwargs["task"]
            if "writing a final report" not in task:
                _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: PASS"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.passed is False
        assert result.error is not None
        assert "Report was not created" in result.error
        assert result.exit_code == 1
        assert result.results[-1].success

    def test_config_can_disable_spec_lint(self, tmp_path):
        config = {**SAMPLE_CONFIG, "plan": {"max_fix_cycles": 0, "spec_lint_enabled": False}}
        planner = Planner(config)
        tasks = []

        def fake_run(**kwargs):
            task = kwargs["task"]
            tasks.append(task)
            _write_prompt_artifact(task, SPEC_R1)
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: PASS"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.passed is True
        assert not any("SPEC REVIEW VERDICT" in task for task in tasks)

    def test_invalid_workspace_and_empty_slug_fail(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        missing = planner.run(task="Task", directory=str(tmp_path / "missing"))
        empty_slug = planner.run(task="!!!", directory=str(tmp_path))
        assert "Working directory" in (missing.error or "")
        assert missing.exit_code == 2
        assert "non-empty" in (empty_slug.error or "")
        assert empty_slug.exit_code == 2

    def test_verify_fails_then_fix_succeeds(self, tmp_path):
        """First verify fails, fix cycle succeeds."""
        planner = Planner(SAMPLE_CONFIG)

        call_count = [0]

        def fake_run(**kwargs):
            call_count[0] += 1
            task = kwargs.get("task", "")
            _write_prompt_artifact(task, SPEC_R1_R2)
            msg = ""
            # Step 3 (review after spec): should be review
            if "SPEC REVIEW VERDICT" in task:
                msg = "SPEC REVIEW VERDICT: PASS"
            # Step 6 (first verify): should fail
            elif "VERDICT" in task and "RE-VERIFICATION" not in task:
                msg = (
                    "| ID | Requirement | Status | Evidence |\n"
                    "|----|-------------|--------|----------|\n"
                    "| R1 | Add endpoint | ✅ Done | file.py |\n"
                    "| R2 | Add tests    | ❌ Not done | missing |\n"
                    "| A1 | Tests pass | ❌ Not done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: FAIL — R2 not done\n"
                )
            # Step 8 (verify after fix): should pass
            elif "VERDICT" in task and "RE-VERIFICATION" in task:
                msg = (
                    "| ID | Requirement | Status | Evidence |\n"
                    "|----|-------------|--------|----------|\n"
                    "| R1 | Add endpoint | ✅ Done | file.py |\n"
                    "| R2 | Add tests    | ✅ Done | test_file.py |\n"
                    "| A1 | Tests pass | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: PASS\n"
                )
            return _make_success_result(msg)

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(
                task="Add tests", directory=str(tmp_path), max_fix_cycles=2
            )

        assert result.passed is True
        assert result.fix_cycles == 1

    def test_partial_reverification_cannot_pass_or_replace_full_matrix(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)
        tasks = []

        def fake_run(**kwargs):
            task = kwargs["task"]
            tasks.append(task)
            _write_prompt_artifact(task, SPEC_R1_R2)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "RE-VERIFICATION" in task:
                return _make_success_result(
                    "| R2 | Tests | ✅ Done | test_file.py |\nVERDICT: PASS"
                )
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| R2 | Tests | ❌ Not done | missing |\n"
                    "| A1 | Tests pass | ❌ Not done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: FAIL — tests missing"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(
                task="Task", directory=str(tmp_path), max_fix_cycles=1
            )

        assert result.passed is False
        assert result.compliance is None
        assert result.compliance_path is not None
        assert not result.compliance_path.exists()
        assert "compliance matrix to include" not in tasks[-1]

    def test_fix_cycles_exhausted(self, tmp_path):
        """Fix cycles run out without resolving issues."""
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            task = kwargs.get("task", "")
            _write_prompt_artifact(task, SPEC_R1)
            msg = ""
            if "SPEC REVIEW VERDICT" in task:
                msg = "SPEC REVIEW VERDICT: PASS"
            elif "VERDICT" in task:
                msg = (
                    "| ID | Requirement | Status | Evidence |\n"
                    "|----|-------------|--------|----------|\n"
                    "| R1 | Broken thing | ❌ Not done | — |\n"
                    "| A1 | Tests pass | ❌ Not done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: FAIL — R1 still broken\n"
                )
            return _make_success_result(msg)

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(
                task="Fix all bugs", directory=str(tmp_path), max_fix_cycles=2
            )

        assert result.passed is False
        assert result.fix_cycles == 2

    def test_configured_fix_cycles_are_used_when_argument_is_none(self, tmp_path):
        config = {**SAMPLE_CONFIG, "plan": {"max_fix_cycles": 1}}
        planner = Planner(config)

        def fake_run(**kwargs):
            task = kwargs["task"]
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ❌ Not done | missing |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: FAIL — R1"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(
                task="Task", directory=str(tmp_path), max_fix_cycles=None
            )

        assert result.fix_cycles == 1
        assert result.iterations == 2

    def test_max_iterations_caps_implementation_fix_attempts(self, tmp_path):
        config = {**SAMPLE_CONFIG, "plan": {"max_fix_cycles": 5}}
        planner = Planner(config)

        def fake_run(**kwargs):
            task = kwargs["task"]
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: PASS")
            if "VERDICT" in task:
                return _make_success_result(
                    "| R1 | Feature | ❌ Not done | missing |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: FAIL — R1"
                )
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(
                task="Task", directory=str(tmp_path), max_iterations=1
            )

        assert result.fix_cycles == 0
        assert result.iterations == 1

    def test_runner_failure_exit_code_is_propagated(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)

        with patch.object(
            planner.runner,
            "run",
            return_value=_make_error_result("OpenHands missing", exit_code=5),
        ):
            result = planner.run(task="Task", directory=str(tmp_path))

        assert result.exit_code == 5

    def test_overall_deadline_stops_pipeline(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            _write_prompt_artifact(kwargs["task"], SPEC_R1)
            return _make_success_result()

        with (
            patch.object(planner.runner, "run", side_effect=fake_run) as runner_run,
            patch("konstruktor.planner.time.monotonic", side_effect=[0, 1, 61]),
        ):
            result = planner.run(task="Task", directory=str(tmp_path), timeout=60)

        assert runner_run.call_count == 1
        assert result.exit_code == 3
        assert "timeout" in (result.error or "").lower()

    def test_review_rejects_and_rewrites_spec(self, tmp_path):
        """A rewritten spec must pass a bounded second review."""
        planner = Planner(SAMPLE_CONFIG)

        review_count = [0]

        def fake_run(**kwargs):
            task = kwargs.get("task", "")
            _write_prompt_artifact(task, "# Specification\nR1. Initial requirement\n")
            msg = ""
            if "SPEC REVIEW VERDICT" in task:
                review_count[0] += 1
                if review_count[0] == 1:
                    spec_path = re.search(r"at `([^`]+)`", task)
                    assert spec_path is not None
                    Path(spec_path.group(1)).write_text(SPEC_R1)
                    msg = "SPEC REVIEW VERDICT: FAIL — R1 too broad, missing out of scope"
                else:
                    msg = "SPEC REVIEW VERDICT: PASS"
            elif "VERDICT" in task:
                msg = (
                    "| ID | Requirement | Status | Evidence |\n"
                    "|----|-------------|--------|----------|\n"
                    "| R1 | Fixed spec  | ✅ Done | file.py |\n"
                    "| A1 | Tests pass | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: PASS\n"
                )
            return _make_success_result(msg)

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Add feature", directory=str(tmp_path))

        assert result.passed is True
        assert review_count[0] == 2

    def test_review_failure_without_rewrite_stops(self, tmp_path):
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            task = kwargs["task"]
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                return _make_success_result("SPEC REVIEW VERDICT: FAIL — ambiguous R1")
            return _make_success_result()

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Add feature", directory=str(tmp_path))

        assert result.error is not None
        assert "did not create or update" in result.error

    def test_instance_dir_structure(self, tmp_path):
        """Verify all artifact paths are created in the spec directory."""
        planner = Planner(SAMPLE_CONFIG)

        def fake_run(**kwargs):
            msg = ""
            task = kwargs.get("task", "")
            _write_prompt_artifact(task, SPEC_R1)
            if "SPEC REVIEW VERDICT" in task:
                msg = "SPEC REVIEW VERDICT: PASS"
            elif "VERDICT" in task:
                msg = (
                    "| R1 | Feature | ✅ Done | file.py |\n"
                    "| A1 | Tests | ✅ Done | pytest |\n"
                    "| O1 | Scope | ✅ Done | git diff |\n"
                    "VERDICT: PASS"
                )
            return _make_success_result(msg)

        with patch.object(planner.runner, "run", side_effect=fake_run):
            result = planner.run(task="Add email validation", directory=str(tmp_path))

        spec_dir = result.spec_dir
        assert spec_dir is not None
        assert spec_dir.name == "add-email-validation"
        assert result.spec_path == spec_dir / "spec.md"
        assert result.plan_path == spec_dir / "plan.md"
        assert result.compliance_path == spec_dir / "compliance.md"
        assert result.report_path == spec_dir / "report.md"


# ══════════════════════════════════════════════════════════════
# run_plan top-level
# ══════════════════════════════════════════════════════════════


class TestRunPlan:
    def test_dry_run(self):
        with patch("konstruktor.config.load_config", return_value=SAMPLE_CONFIG):
            exit_code = run_plan(task="test", dry_run=True)
            assert exit_code == 0

    def test_missing_api_key(self):
        import copy
        cfg = copy.deepcopy(SAMPLE_CONFIG)
        cfg["llm"]["api_key"] = ""
        with patch("konstruktor.config.load_config", return_value=cfg):
            exit_code = run_plan(task="test")
            assert exit_code == 4

    def test_planner_error_exit_code_is_returned(self):
        planner_result = PlanResult(task="test", error="timed out", exit_code=3)
        with (
            patch("konstruktor.config.load_config", return_value=SAMPLE_CONFIG),
            patch("konstruktor.planner.Planner.run", return_value=planner_result),
        ):
            exit_code = run_plan(task="test")

        assert exit_code == 3

    def test_json_summary_contains_pipeline_context(self, tmp_path):
        step = _make_success_result("done")
        step.summary.run_id = "run-step"
        step.summary.total_steps = 4
        result = PlanResult(task="test", passed=True, iterations=1, results=[step])
        captured = {}

        def format_summary(summary, config):
            captured.update(summary.to_dict())
            return "{}"

        with (
            patch("konstruktor.config.load_config", return_value=SAMPLE_CONFIG),
            patch("konstruktor.planner.Planner.run", return_value=result),
            patch("konstruktor.reporter.format_json_report", side_effect=format_summary),
        ):
            exit_code = run_plan(
                task="test", directory=str(tmp_path), model="model-x", json_output=True
            )

        assert exit_code == 0
        assert captured["run_id"] == "run-step"
        assert captured["task"] == "test"
        assert captured["directory"] == str(tmp_path.resolve())
        assert captured["model"] == "model-x"
        assert captured["total_steps"] == 4
