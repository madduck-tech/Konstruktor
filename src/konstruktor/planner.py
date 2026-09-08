"""Plan → Execute → Verify → Report — multi-step autonomous workflow.

Spec-driven development pipeline as recommended by best practices:
  Intent → Spec → Review → Plan → Implement → Verify (compliance matrix) → Fix → Report
"""

from __future__ import annotations

import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from konstruktor.logger import get_logger
from konstruktor.parser import RunSummary
from konstruktor.runner import KonstruktorRunner, RunResult

# ═══════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ComplianceItem:
    """A single item in the compliance matrix."""
    id: str                          # e.g. "R1", "A2", "O3"
    requirement: str                 # requirement text
    status: str = ""                 # "Done", "Not done", "Partially done"
    evidence: str = ""               # file path, test name, command output

    @property
    def is_done(self) -> bool:
        return self.status == "✅ Done" and bool(self.evidence.strip())


@dataclass
class ComplianceMatrix:
    """Parsed compliance matrix from agent output."""
    items: list[ComplianceItem] = field(default_factory=list)

    @property
    def all_done(self) -> bool:
        return bool(self.items) and all(item.is_done for item in self.items)

    @property
    def failed_ids(self) -> list[str]:
        return [item.id for item in self.items if not item.is_done]

    @property
    def failed_descriptions(self) -> str:
        lines = []
        for item in self.items:
            if not item.is_done:
                lines.append(f"  - {item.id}: {item.requirement}")
        return "\n".join(lines) if lines else ""

    def concrete_failures(self, expected_ids: list[str]) -> list[ComplianceItem]:
        """Return explicit implementation failures, excluding malformed rows."""
        failed_statuses = {"❌ Not done", "⚠️ Partially done"}
        return [
            item
            for item in self.items
            if item.id in expected_ids and item.status in failed_statuses
        ]


@dataclass
class PlanResult:
    """Result of a full plan → execute → verify cycle."""
    task: str
    spec_dir: Path | None = None
    spec_path: Path | None = None
    plan_path: Path | None = None
    compliance_path: Path | None = None
    report_path: Path | None = None
    spec_content: str = ""
    plan_content: str = ""
    compliance: ComplianceMatrix | None = None
    report_content: str = ""
    iterations: int = 0
    fix_cycles: int = 0
    passed: bool = False
    results: list[RunResult] = field(default_factory=list)
    error: str | None = None
    exit_code: int = 1


# ═══════════════════════════════════════════════════════════════════
# Spec quality checklist
# ═══════════════════════════════════════════════════════════════════

_SPEC_LINT_CHECKLIST = [
    "Each requirement is numbered (R1, R2, ...)",
    "Each requirement is testable / verifiable",
    "Acceptance criteria are explicit and numbered (A1, A2, ...)",
    "Out of Scope section exists with numbered items (O1, O2, ...)",
    "No large code blocks are included (small JSON/SQL examples are OK)",
    "External behavior is described before internal design",
    "Verification commands are listed",
    "Existing project patterns are referenced where applicable",
    "No ambiguous words: 'normal', 'good', 'proper', 'etc', 'as needed'",
]

# ═══════════════════════════════════════════════════════════════════
# Prompt templates
# ═══════════════════════════════════════════════════════════════════

_INTENT_PROMPT = """You are analyzing a task before writing a detailed specification.

TASK: {task}

Write a short intent document to `{intent_path}` with EXACTLY these sections:

# Intent

## Goal
One paragraph — what should exist after this task is done.

## User-visible result
What the user/developer will see or be able to do.

## Non-goals
What we explicitly do NOT want to change or add.

Keep it concise — this is for scoping, not implementation.
Always write or overwrite the target file, even when it already has the desired content."""


_SPEC_PROMPT = """You are writing a detailed implementation specification.

Read the intent at `{intent_path}`. Then analyze the codebase and write
a specification to `{spec_path}`.

The specification MUST follow this exact structure:

# Specification: <short title>

## Goal
One paragraph — what should exist after implementation.

## Context
Relevant files, modules, patterns already in the codebase.
Reference specific files and classes.

## Functional Requirements
Numbered list of concrete, testable requirements:
R1. <requirement>
R2. <requirement>
...

Each requirement must be:
- Specific enough that a verdict (done/not done) is mechanical.
- Describing WHAT, not HOW.
- No code blocks — describe behavior, not implementation.

## Acceptance Criteria
Numbered list of pass/fail conditions:
A1. <criterion>
A2. <criterion>
...

Each criterion must be verifiable by running a command or inspecting a file.
Include expected outputs or behaviors explicitly.

## Out of Scope
Numbered list of what we explicitly do NOT change:
O1. <out-of-scope item>
O2. <out-of-scope item>

This is CRITICAL — it prevents unwanted changes and scope creep.

## Constraints
C1. Use existing error handling pattern from <file/class>
C2. Do not add new libraries unless explicitly required
C3. Do not change existing public API contracts
... (add project-specific constraints)

## Verification Commands
V1. <command to run tests>
V2. <command to lint>
V3. <other verification>

## Definition of Done
The task is done only when:
- All R requirements are implemented
- All A acceptance criteria are satisfied
- Verification commands pass (or failures are explained)
- Compliance matrix is completed
- No out-of-scope changes were made

---
IMPORTANT RULES:
- Number EVERY requirement, criterion, and out-of-scope item.
- Write requirements so they can be verified mechanically.
- Do NOT include large code blocks (small JSON/SQL contracts are OK).
- If you reference existing code, give specific file paths.
- Avoid vague words: 'properly', 'well', 'nice', 'etc.', 'as needed'.
- Always write or overwrite the target file, even when an existing spec is identical.
"""


_SPEC_REVIEW_PROMPT = """You are reviewing a specification before implementation begins.

Read the specification at `{spec_path}`.

Perform a quality review. Check each item in this checklist:

{checklist}

After reviewing, output:

SPEC REVIEW VERDICT: PASS
or
SPEC REVIEW VERDICT: FAIL — <list each problem>

If the spec has problems:
1. Describe each problem clearly.
2. REWRITE the specification at `{spec_path}` to fix all problems.
3. Keep the same structure (Goal, Context, R1..Rn, A1..An, O1..On, etc.).
4. If requirements are missing acceptance criteria, add them.
5. If requirements are too broad, split them.
6. If ambiguous words are found, replace them with concrete criteria.

If the spec is already good, just output PASS and proceed.
Do NOT implement anything — only review and fix the spec."""


_PLAN_PROMPT = """You are writing an implementation plan based on a specification.

Read the specification at `{spec_path}`.
Then write a short implementation plan to `{plan_path}`.

The plan must be CONCRETE and FILE-LEVEL:

# Implementation Plan

## Files to inspect (existing patterns to follow)
1. `path/to/file` — to understand <pattern>
2. `path/to/file` — to understand <pattern>

## Files to create/modify (in order)
1. `path/to/file` — <what to do> (create / modify)
2. `path/to/file` — <what to do> (create / modify)

## Test plan
- Test: <scenario> → expected result
- Test: <scenario> → expected result

## Order of operations
1. First, ...
2. Then, ...
3. Finally, ...

---
RULES:
- Do NOT add vague items like 'improve architecture' or 'make it better'.
- Every item must reference a specific file or test.
- Do NOT implement anything — only plan.
- Always write or overwrite the target plan file, even when it is already correct.
"""


_IMPLEMENT_PROMPT = """You are implementing a specification.

Read the specification at `{spec_path}`.
Read the implementation plan at `{plan_path}`.

Then implement ALL changes required by the specification.

CRITICAL RULES:
1. Implement ONLY what is explicitly required by the specification.
2. Do NOT refactor unrelated code.
3. Do NOT introduce new abstractions unless explicitly required.
4. Prefer existing project patterns over new patterns.
5. Do NOT add new libraries unless the spec requires it.
6. If you discover a better but broader change, NOTE it but do NOT implement it.
7. Do NOT change anything listed in Out of Scope.
8. Each change must correspond to a requirement (R1..Rn).
9. Do NOT ask questions — use your best judgment for any ambiguities.
"""


_VERIFY_PROMPT = """You are verifying an implementation against its specification.

Read the specification at `{spec_path}`.

Verify the current codebase against EVERY requirement (R1..Rn)
and acceptance criterion (A1..An) in the specification.

{fix_instruction}

Produce a COMPLIANCE MATRIX using EXACTLY this format:

| ID | Requirement | Status | Evidence |
|----|-------------|--------|----------|
| R1 | <requirement text> | ✅ Done | <file:line or command> |
| R2 | <requirement text> | ✅ Done | <file:line or command> |
| A1 | <criterion text> | ✅ Done | <test name or command output> |
| O1 | <out-of-scope text> | ✅ Done | <no changes to that area> |

Status must be one of:
- ✅ Done — fully implemented and verified
- ❌ Not done — not implemented or failing
- ⚠️ Partially done — partially implemented

Evidence must be specific: file path, line number, test name, or command output.
Do NOT write vague evidence like "looks good" or "seems correct".

After the matrix, output:

VERDICT: PASS
or
VERDICT: FAIL — <summary of what failed>

If any item is ❌ Not done or ⚠️ Partially done, the verdict MUST be FAIL.
"""


_FIX_PROMPT = """You are fixing specific failed requirements from a compliance matrix.

The following requirements/criteria are NOT DONE:

{failed_items}

Fix ONLY these specific items. CRITICAL RULES:
1. Do NOT reimplement the whole task.
2. Do NOT change anything that is already ✅ Done.
3. Before editing, confirm which failed ID you are fixing.
4. The following verification will recreate the full compliance matrix.
5. Do NOT touch files unrelated to the failed items.
"""


_REPORT_PROMPT = """You are writing a final report for a completed (or terminated) task.

Read the specification at `{spec_path}`.
Read the compliance matrix at `{compliance_path}` (if it exists).

Run `git diff --stat` to see all changed files.

Then write a final report to `{report_path}` with these sections:

## Summary
One paragraph — what was accomplished.

## Files Changed
| File | Change Level | Description |
|---|---|---|
| ... | 🔴 Major / 🟡 Moderate / 🟢 Minor | ... |

Change levels:
- 🔴 Major (>50 lines, new files, or significant changes)
- 🟡 Moderate (10-50 lines)
- 🟢 Minor (<10 lines, formatting, imports)

## Compliance Summary
| ID | Requirement | Status |
|---|---|---|
| ... | ... | ✅ / ❌ / ⚠️ |

(Include the full compliance matrix here)

## Unresolved Issues
If any requirements remain Not Done, explain why and what to do next.

## Known Limitations / Risks
Any tradeoffs, edge cases not covered, or decisions that need human review.

## Out-of-Scope Suggestions
If during implementation you noticed broader improvements that could be made
but were out of scope, list them here.

CONCISE — this is for a human to quickly understand what happened.
Always write or overwrite the target report file, even when it already exists.
"""


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

_TRANSIENT_MARKERS = [
    "upstream request failed",
    "rate limit",
    "rate_limit",
    "too many requests",
    "service unavailable",
    "server overloaded",
    "internal server error",
    "connection reset",
    "connection error",
    "timed out",
    "try again",
]


def _slugify(task: str) -> str:
    """Create a filesystem-safe slug from a task description."""
    slug = re.sub(r"[^\w\s-]", "", task.lower())
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug.strip("-")[:80]


def _is_transient_error(error_text: str) -> bool:
    """Check if an error is a transient provider issue worth retrying."""
    error_lower = error_text.lower()
    return any(marker in error_lower for marker in _TRANSIENT_MARKERS)


def _parse_compliance_matrix(text: str) -> ComplianceMatrix:
    """Parse a compliance matrix from agent output.

    Looks for markdown table rows with ID, Requirement, Status, Evidence.
    """
    matrix = ComplianceMatrix()

    # Find table rows: | R1 | requirement text | status | evidence |
    # Regex matches markdown table rows with at least 4 columns
    row_pattern = re.compile(
        r"^\|\s*"           # opening pipe
        r"([RrAaOo]\d+)"    # ID: R1, A2, O3 etc.
        r"\s*\|\s*"
        r"(.+?)"            # requirement text
        r"\s*\|\s*"
        r"(.+?)"            # status
        r"\s*\|\s*"
        r"(.*?)"            # evidence
        r"\s*\|",
        re.MULTILINE,
    )

    for match in row_pattern.finditer(text):
        row_text = match.group(0)
        if re.search(r"ID\s*\|\s*Requirement", row_text):
            continue
        # Skip separator rows like |----|----|----|----|
        if re.match(r"^\|[\s\-]+\|[\s\-]+\|[\s\-]+\|[\s\-]+\|", row_text):
            continue

        req_id = match.group(1).strip().upper()
        requirement = match.group(2).strip()
        status = match.group(3).strip()
        evidence = match.group(4).strip()

        normalized_statuses = {
            "✅ done": "✅ Done",
            "❌ not done": "❌ Not done",
            "⚠️ partially done": "⚠️ Partially done",
        }
        status = normalized_statuses.get(status.casefold(), status)

        matrix.items.append(ComplianceItem(
            id=req_id,
            requirement=requirement,
            status=status,
            evidence=evidence,
        ))

    return matrix


def _parse_review_verdict(text: str) -> tuple[bool, str]:
    """Parse spec review verdict. Returns (passed, issues)."""
    verdicts = re.findall(
        r"(?im)^\s*SPEC\s+REVIEW\s+VERDICT\s*:\s*(PASS|FAIL)"
        r"(?:\s*[-–—]\s*([^\n]+))?\s*$",
        text,
    )
    if len(verdicts) == 1:
        verdict, issues = verdicts[0]
        if verdict.upper() == "PASS":
            return True, ""
        return False, issues.strip() or "Specification review failed"
    if verdicts:
        return False, "Contradictory review verdicts"
    return False, "Could not parse review verdict"


def _parse_verdict(text: str) -> tuple[bool, str]:
    """Parse verification verdict from agent output. Returns (passed, issues)."""
    verdicts = re.findall(
        r"(?im)^\s*VERDICT\s*:\s*(PASS|FAIL)(?:\s*[-–—]\s*([^\n]+))?\s*$",
        text,
    )
    if len(verdicts) == 1:
        verdict, issues = verdicts[0]
        if verdict.upper() == "PASS":
            return True, ""
        return False, issues.strip() or "Verification failed"
    if verdicts:
        return False, "Contradictory verification verdicts"
    return False, text[:500] if text else "No verification output"


def _expected_compliance_ids(spec: str) -> tuple[list[str], str | None]:
    """Extract R/A/O IDs from their required specification sections."""
    required_sections = {
        "functional requirements": "R",
        "acceptance criteria": "A",
        "out of scope": "O",
    }
    found_sections: set[str] = set()
    ids: list[str] = []
    current_section: str | None = None

    for line in spec.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current_section = heading.group(1).strip().casefold()
            if current_section in required_sections:
                found_sections.add(current_section)
            continue
        item = re.match(r"^\s*([RAO]\d+)\.\s+\S", line)
        if item and current_section in required_sections:
            req_id = item.group(1)
            if req_id.startswith(required_sections[current_section]):
                ids.append(req_id)

    missing = [name for name in required_sections if name not in found_sections]
    if missing:
        return [], f"Specification is missing required sections: {', '.join(missing)}"
    missing_prefixes = [
        prefix for prefix in required_sections.values()
        if not any(req_id.startswith(prefix) for req_id in ids)
    ]
    if missing_prefixes:
        return [], f"Specification is missing required IDs: {', '.join(missing_prefixes)}"
    return ids, None


def _validate_compliance(
    compliance: ComplianceMatrix,
    expected_ids: list[str],
) -> tuple[bool, str]:
    """Require one valid, evidenced row for every spec R/A/O item."""
    actual_ids = [item.id for item in compliance.items]
    if not expected_ids:
        return False, "Specification contains no R/A/O requirements"
    if len(expected_ids) != len(set(expected_ids)):
        return False, "Specification contains duplicate R/A/O IDs"
    if len(actual_ids) != len(set(actual_ids)):
        return False, "Compliance matrix contains duplicate IDs"
    if set(actual_ids) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(actual_ids))
        extra = sorted(set(actual_ids) - set(expected_ids))
        return False, f"Compliance coverage mismatch; missing={missing}, extra={extra}"
    if not compliance.all_done:
        return False, compliance.failed_descriptions or "Compliance matrix has invalid rows"
    return True, ""


def _has_exact_coverage(compliance: ComplianceMatrix, expected_ids: list[str]) -> bool:
    actual_ids = [item.id for item in compliance.items]
    return (
        bool(expected_ids)
        and len(expected_ids) == len(set(expected_ids))
        and len(actual_ids) == len(set(actual_ids))
        and set(actual_ids) == set(expected_ids)
    )


def _artifact_state(path: Path) -> tuple[int, int, int, int] | None:
    """Return filesystem identity/change metadata for an artifact."""
    if not path.exists():
        return None
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _fresh_artifact_error(
    step: str,
    path: Path,
    previous: tuple[int, int, int, int] | None,
) -> str | None:
    """Validate that a producer created or updated a non-empty artifact."""
    current = _artifact_state(path)
    if current is None:
        return f"{step.capitalize()} was not created at {path}"
    if not path.read_text().strip():
        return f"{step.capitalize()} artifact is empty at {path}"
    if current == previous:
        return f"{step.capitalize()} did not create or update {path}"
    return None


def _step_error(step: str, result: RunResult) -> str:
    detail = result.summary.error or result.summary.final_message or "unknown agent failure"
    return f"{step.capitalize()} step failed: {detail[:400]}"


def _timeout_result(workspace: Path) -> RunResult:
    summary = RunSummary(
        status="timeout",
        exit_code=3,
        error="Overall plan timeout exceeded",
    )
    return RunResult(summary=summary, events=[], output_dir=workspace)


# ═══════════════════════════════════════════════════════════════════
# Planner
# ═══════════════════════════════════════════════════════════════════


class Planner:
    """Multi-step planner implementing spec-driven development.

    Pipeline:
      Intent → Spec → Review → Plan → Implement → Verify → Fix → Report
    """

    def __init__(self, config: dict):
        self.config = config
        self.log = get_logger("konstruktor.planner")
        self.runner = KonstruktorRunner(config)

    def run(
        self,
        task: str,
        directory: str,
        model: str | None = None,
        timeout: int | None = None,
        output_dir: str | None = None,
        max_iterations: int | None = None,
        max_fix_cycles: int | None = None,
        spec_lint_enabled: bool | None = None,
        dry_run: bool = False,
        approve: str = "auto",
    ) -> PlanResult:
        """Execute the full spec-driven pipeline.

        Args:
            task: High-level task description.
            directory: Working directory.
            max_iterations: Max total implement→verify attempts, including fixes.
            max_fix_cycles: Max fix-only loops after initial verification reveals failures.
            dry_run: If True, only plan without executing.
            approve: Approval mode.
        """
        workspace = Path(directory).expanduser().resolve()
        plan_result = PlanResult(task=task)
        if not workspace.exists() or not workspace.is_dir():
            plan_result.error = (
                f"Working directory does not exist or is not a directory: {workspace}"
            )
            plan_result.exit_code = 2
            return plan_result

        slug = _slugify(task)
        if not slug:
            plan_result.error = "Task must produce a non-empty filesystem slug"
            plan_result.exit_code = 2
            return plan_result

        plan_config = self.config.get("plan", {})
        if max_iterations is None:
            max_iterations = int(plan_config.get("max_iterations", 3))
        if max_iterations < 1:
            plan_result.error = "max_iterations must be positive"
            plan_result.exit_code = 2
            return plan_result
        if max_fix_cycles is None:
            max_fix_cycles = int(plan_config.get("max_fix_cycles", 2))
        if max_fix_cycles < 0:
            plan_result.error = "max_fix_cycles must be non-negative"
            plan_result.exit_code = 2
            return plan_result
        if spec_lint_enabled is None:
            spec_lint_enabled = bool(plan_config.get("spec_lint_enabled", True))
        overall_timeout = timeout if timeout is not None else self.config["limits"]["timeout"]
        if overall_timeout <= 0:
            plan_result.error = "timeout must be positive"
            plan_result.exit_code = 2
            return plan_result
        self._plan_deadline = time.monotonic() + overall_timeout

        # New structure: docs/specs/<slug>/ for all artifacts
        spec_dir = workspace / "docs" / "specs" / slug
        intent_path = spec_dir / "intent.md"
        spec_path = spec_dir / "spec.md"
        plan_path = spec_dir / "plan.md"
        compliance_path = spec_dir / "compliance.md"
        report_path = spec_dir / "report.md"

        plan_result.spec_dir = spec_dir
        plan_result.spec_path = spec_path
        plan_result.plan_path = plan_path
        plan_result.compliance_path = compliance_path
        plan_result.report_path = report_path

        # Ensure spec directory exists (for dry-run we don't create)
        if not dry_run:
            spec_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 0: Intent ──
        self.log.info("plan_step", step="intent")
        intent_before = _artifact_state(intent_path) if not dry_run else None
        intent_task = _INTENT_PROMPT.format(task=task, intent_path=str(intent_path))
        intent_result = self._run_step(
            "intent", intent_task, workspace, model, timeout, output_dir, dry_run, approve
        )
        plan_result.results.append(intent_result)
        if not intent_result.success:
            plan_result.error = _step_error("intent", intent_result)
            plan_result.exit_code = intent_result.exit_code
            return plan_result
        if not dry_run:
            plan_result.error = _fresh_artifact_error("intent", intent_path, intent_before)
            if plan_result.error:
                return plan_result

        # ── Step 1: Specification ──
        self.log.info("plan_step", step="spec")
        spec_before = _artifact_state(spec_path) if not dry_run else None
        spec_task = _SPEC_PROMPT.format(
            intent_path=str(intent_path),
            spec_path=str(spec_path),
        )
        spec_result = self._run_step(
            "spec", spec_task, workspace, model, timeout, output_dir, dry_run, approve
        )
        plan_result.results.append(spec_result)
        if not spec_result.success:
            plan_result.error = _step_error("spec", spec_result)
            plan_result.exit_code = spec_result.exit_code
            return plan_result
        if not dry_run:
            plan_result.error = _fresh_artifact_error("spec", spec_path, spec_before)
            if plan_result.error:
                return plan_result
            plan_result.spec_content = spec_path.read_text()
            self.log.info("plan_spec_saved", path=str(spec_path))

        if dry_run:
            plan_result.exit_code = 0
            return plan_result

        # ── Step 2: Spec Review & Lint ──
        if spec_lint_enabled:
            self.log.info("plan_step", step="review")
            checklist = "\n".join(f"- [ ] {item}" for item in _SPEC_LINT_CHECKLIST)
            review_task = _SPEC_REVIEW_PROMPT.format(
                spec_path=str(spec_path),
                checklist=checklist,
            )
            review_before = _artifact_state(spec_path)
            review_result = self._run_step(
                "review", review_task, workspace, model, timeout, output_dir, dry_run, approve
            )
            plan_result.results.append(review_result)
            if not review_result.success:
                plan_result.error = _step_error("review", review_result)
                plan_result.exit_code = review_result.exit_code
                return plan_result

            review_output = review_result.summary.final_message or ""
            review_passed, review_issues = _parse_review_verdict(review_output)
            if not review_passed:
                fresh_error = _fresh_artifact_error("review", spec_path, review_before)
                if fresh_error:
                    plan_result.error = f"{review_issues}; {fresh_error}"
                    return plan_result
                plan_result.spec_content = spec_path.read_text()
                review_result = self._run_step(
                    "review-correction", review_task, workspace, model, timeout,
                    output_dir, dry_run, approve,
                )
                plan_result.results.append(review_result)
                if not review_result.success:
                    plan_result.error = _step_error("review correction", review_result)
                    plan_result.exit_code = review_result.exit_code
                    return plan_result
                review_passed, review_issues = _parse_review_verdict(
                    review_result.summary.final_message or ""
                )
                if not review_passed:
                    plan_result.error = (
                        f"Specification review failed after correction: {review_issues}"
                    )
                    return plan_result

        plan_result.spec_content = spec_path.read_text()
        expected_ids, spec_structure_error = _expected_compliance_ids(
            plan_result.spec_content
        )
        if spec_structure_error:
            plan_result.error = spec_structure_error
            return plan_result

        # ── Step 3: Implementation Plan ──
        self.log.info("plan_step", step="plan")
        plan_before = _artifact_state(plan_path)
        plan_task = _PLAN_PROMPT.format(
            spec_path=str(spec_path),
            plan_path=str(plan_path),
        )
        plan_result_obj = self._run_step(
            "plan", plan_task, workspace, model, timeout, output_dir, dry_run, approve
        )
        plan_result.results.append(plan_result_obj)
        if not plan_result_obj.success:
            plan_result.error = _step_error("plan", plan_result_obj)
            plan_result.exit_code = plan_result_obj.exit_code
            return plan_result
        plan_result.error = _fresh_artifact_error("plan", plan_path, plan_before)
        if plan_result.error:
            return plan_result
        plan_result.plan_content = plan_path.read_text()

        # ── Step 4: Implementation ──
        self.log.info("plan_step", step="implement")
        impl_task = _IMPLEMENT_PROMPT.format(
            spec_path=str(spec_path),
            plan_path=str(plan_path),
        )
        impl_result = self._run_step(
            "implement", impl_task, workspace, model, timeout, output_dir, dry_run, approve
        )
        plan_result.results.append(impl_result)
        if not impl_result.success:
            plan_result.error = _step_error("implement", impl_result)
            plan_result.exit_code = impl_result.exit_code
            return plan_result

        # ── Step 5: Verification → Compliance Matrix ──
        verify_task_first = _VERIFY_PROMPT.format(
            spec_path=str(spec_path),
            fix_instruction="",
        )
        compliance_path.unlink(missing_ok=True)
        verify_result = self._run_step(
            "verify", verify_task_first, workspace, model, timeout, output_dir, dry_run, approve
        )
        plan_result.results.append(verify_result)
        if not verify_result.success:
            plan_result.error = _step_error("verify", verify_result)
            plan_result.exit_code = verify_result.exit_code
            return plan_result

        # Parse compliance matrix and verdict
        verify_output = verify_result.summary.final_message or verify_result.summary.error or ""
        compliance = _parse_compliance_matrix(verify_output)
        verdict_passed, _ = _parse_verdict(verify_output)
        matrix_passed, _ = _validate_compliance(compliance, expected_ids)
        passed = verdict_passed and matrix_passed

        # Save compliance matrix
        if _has_exact_coverage(compliance, expected_ids):
            plan_result.compliance = compliance
            self._save_compliance(compliance, compliance_path)

        plan_result.iterations = 1
        if passed:
            plan_result.passed = True
            self.log.info("plan_passed", iteration=1)
        else:
            # ── Fix loop ──
            concrete_failures = compliance.concrete_failures(expected_ids)
            self.log.info(
                "plan_verify_failed",
                iteration=1,
                failed_ids=[item.id for item in concrete_failures],
            )

            fix_limit = min(max_fix_cycles, max_iterations - 1)
            for fix_cycle in range(1, fix_limit + 1):
                if not concrete_failures:
                    break
                plan_result.fix_cycles = fix_cycle
                plan_result.iterations = fix_cycle + 1
                self.log.info("plan_fix_cycle", cycle=fix_cycle)

                # Build fix prompt with only failed items
                failed_descriptions = "\n".join(
                    f"  - {item.id}: {item.requirement}"
                    for item in concrete_failures
                )

                fix_task = _FIX_PROMPT.format(failed_items=failed_descriptions)
                fix_result = self._run_step(
                    f"fix-{fix_cycle}", fix_task, workspace, model, timeout,
                    output_dir, dry_run, approve
                )
                plan_result.results.append(fix_result)
                if not fix_result.success:
                    plan_result.error = _step_error(f"fix-{fix_cycle}", fix_result)
                    plan_result.exit_code = fix_result.exit_code
                    break

                # Re-verify
                compliance_path.unlink(missing_ok=True)
                plan_result.compliance = None
                verify_task_retry = _VERIFY_PROMPT.format(
                    spec_path=str(spec_path),
                    fix_instruction=(
                        "This is a RE-VERIFICATION after fixing failed requirements. "
                        "Re-check the FULL specification and output the FULL matrix, including "
                        "all R, A, and O rows, not only previously failed items."
                    ),
                )
                verify_result = self._run_step(
                    f"verify-{fix_cycle}", verify_task_retry, workspace, model, timeout,
                    output_dir, dry_run, approve
                )
                plan_result.results.append(verify_result)
                if not verify_result.success:
                    plan_result.error = _step_error(f"verify-{fix_cycle}", verify_result)
                    plan_result.exit_code = verify_result.exit_code
                    break

                final_msg = verify_result.summary.final_message
                err_msg = verify_result.summary.error or ""
                verify_output = final_msg or err_msg
                compliance = _parse_compliance_matrix(verify_output)
                verdict_passed, _ = _parse_verdict(verify_output)
                matrix_passed, _ = _validate_compliance(compliance, expected_ids)
                passed = verdict_passed and matrix_passed

                if _has_exact_coverage(compliance, expected_ids):
                    plan_result.compliance = compliance
                    self._save_compliance(compliance, compliance_path)

                if passed:
                    plan_result.passed = True
                    self.log.info("plan_passed_after_fix", fix_cycle=fix_cycle)
                    break

                concrete_failures = compliance.concrete_failures(expected_ids)

                self.log.info(
                    "plan_fix_still_failing",
                    fix_cycle=fix_cycle,
                    failed_ids=[item.id for item in concrete_failures],
                )
            else:
                # Exhausted fix cycles
                plan_result.passed = False
                self.log.warning(
                    "plan_fix_exhausted",
                    max_fix_cycles=fix_limit,
                    remaining_failures=[item.id for item in concrete_failures],
                )

        # ── Final: Generate report ──
        self.log.info("plan_step", step="report")
        report_result, report, report_error = self._generate_report(
            workspace=workspace,
            spec_path=spec_path,
            compliance_path=compliance_path,
            report_path=report_path,
            task=task,
            passed=plan_result.passed,
            iterations=plan_result.iterations,
            fix_cycles=plan_result.fix_cycles,
            compliance=plan_result.compliance,
            model=model,
            timeout=timeout,
            output_dir=output_dir,
            dry_run=dry_run,
            approve=approve,
        )
        plan_result.results.append(report_result)
        plan_result.report_content = report
        if report_error:
            plan_result.passed = False
            plan_result.error = report_error
            plan_result.exit_code = report_result.exit_code if not report_result.success else 1
        elif plan_result.passed:
            plan_result.exit_code = 0

        return plan_result

    # ═══════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════

    def _save_compliance(self, compliance: ComplianceMatrix, path: Path) -> None:
        """Save compliance matrix as a markdown file."""
        lines = [
            "# Compliance Matrix",
            "",
            "| ID | Requirement | Status | Evidence |",
            "|----|-------------|--------|----------|",
        ]
        for item in compliance.items:
            lines.append(
                f"| {item.id} | {item.requirement} | {item.status} | {item.evidence} |"
            )
        path.write_text("\n".join(lines) + "\n")

    def _generate_report(
        self,
        workspace: Path,
        spec_path: Path,
        compliance_path: Path,
        report_path: Path,
        task: str,
        passed: bool,
        iterations: int,
        fix_cycles: int,
        compliance: ComplianceMatrix | None,
        model: str | None,
        timeout: int | None,
        output_dir: str | None,
        dry_run: bool,
        approve: str,
    ) -> tuple[RunResult, str, str | None]:
        """Generate final report via the agent."""
        report_task = _REPORT_PROMPT.format(
            spec_path=str(spec_path),
            compliance_path=str(compliance_path),
            report_path=str(report_path),
        )

        # If we already have a compliance matrix, prepend it to help the agent
        if compliance and compliance.items:
            matrix_text = "\n".join(
                f"| {item.id} | {item.requirement} | {item.status} | {item.evidence} |"
                for item in compliance.items
            )
            report_task += (
                f"\n\nHere is the compliance matrix to include:\n\n"
                f"| ID | Requirement | Status | Evidence |\n"
                f"|----|-------------|--------|----------|\n"
                f"{matrix_text}\n"
            )

        report_task += (
            f"\n\nContext: "
            f"Final verdict is {'PASS' if passed else 'FAIL'} "
            f"after {iterations} implementation iteration(s) "
            f"and {fix_cycles} fix cycle(s)."
        )

        self.log.info("plan_step", step="report")
        report_before = _artifact_state(report_path)
        report_result = self._run_step(
            "report", report_task, workspace, model, timeout, output_dir, dry_run, approve
        )
        if not report_result.success:
            return report_result, "", _step_error("report", report_result)
        freshness_error = _fresh_artifact_error("report", report_path, report_before)
        if freshness_error:
            return report_result, "", freshness_error
        return report_result, report_path.read_text(), None

    def _run_step(
        self,
        step_name: str,
        task: str,
        workspace: Path,
        model: str | None,
        timeout: int | None,
        output_dir: str | None,
        dry_run: bool,
        approve: str,
        max_retries: int = 2,
    ) -> RunResult:
        """Run a single step via KonstruktorRunner, with retry for transient errors."""
        last_result: RunResult | None = None

        for attempt in range(max_retries + 1):
            remaining = self._plan_deadline - time.monotonic()
            if remaining <= 0:
                return _timeout_result(workspace)
            self.log.info("plan_step_exec", step=step_name, attempt=attempt + 1)
            result = self.runner.run(
                task=task,
                directory=str(workspace),
                model=model,
                timeout=max(1, math.ceil(remaining)),
                output_dir=output_dir,
                dry_run=dry_run,
                approve=approve,
            )
            last_result = result

            if time.monotonic() >= self._plan_deadline:
                return _timeout_result(workspace)
            if result.success:
                return result

            # Check for transient error worth retrying
            error_text = result.summary.error or result.summary.final_message or ""
            if _is_transient_error(error_text) and attempt < max_retries:
                wait = 5 * (attempt + 1)
                self.log.warning(
                    "plan_transient_error",
                    step=step_name,
                    attempt=attempt + 1,
                    retry_in=wait,
                    error=error_text[:200],
                )
                print(
                    f"  ⚠️  Transient error in '{step_name}' "
                    f"(attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {wait}s...\n"
                    f"  → {error_text[:200]}",
                    file=sys.stderr,
                )
                if time.monotonic() + wait >= self._plan_deadline:
                    return _timeout_result(workspace)
                time.sleep(wait)
                continue

            return result

        return last_result  # type: ignore[return-value]


def _diagnose_missing_file(step: str, path: Path, result: RunResult) -> str:
    """Produce a helpful diagnostic when a step fails to create its output file."""
    error_text = result.summary.error or result.summary.final_message or ""
    if _is_transient_error(error_text):
        return (
            f"{step.capitalize()} creation failed: LLM provider returned a transient error.\n"
            f"  → {error_text[:300]}\n"
            f"  → All retry attempts were exhausted.\n"
            f"  → Try again later — this is a provider-side issue, not a bug."
        )
    elif error_text:
        return (
            f"{step.capitalize()} was not created at {path}\n"
            f"  Agent error: {error_text[:400]}"
        )
    else:
        return (
            f"{step.capitalize()} was not created at {path}\n"
            f"  Agent completed but did not write the file.\n"
            f"  → Check if the directory is writable.\n"
            f"  → Full agent log: konstruktor-output/"
        )


# ═══════════════════════════════════════════════════════════════════
# Top-level entry point
# ═══════════════════════════════════════════════════════════════════


def run_plan(
    task: str,
    directory: str = ".",
    model: str | None = None,
    config: str | None = None,
    timeout: int | None = None,
    max_iterations: int | None = None,
    max_fix_cycles: int | None = None,
    spec_lint_enabled: bool | None = None,
    output: str | None = None,
    json_output: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    approve: str = "auto",
) -> int:
    """Top-level function for plan mode. Returns exit code."""
    from konstruktor.config import load_config, validate_config
    from konstruktor.reporter import format_json_report, format_text_report, save_summary

    cfg = load_config(config)
    errors = validate_config(cfg)
    if errors:
        for err in errors:
            print(f"Configuration error: {err}", file=sys.stderr)
        return 4

    if verbose:
        cfg["logging"]["level"] = "debug"

    planner = Planner(cfg)
    result = planner.run(
        task=task,
        directory=directory,
        model=model,
        timeout=timeout,
        output_dir=output,
        max_iterations=max_iterations,
        max_fix_cycles=max_fix_cycles,
        spec_lint_enabled=spec_lint_enabled,
        dry_run=dry_run,
        approve=approve,
    )

    if dry_run:
        slug = _slugify(task)
        spec_dir = Path(directory).resolve() / "docs" / "specs" / slug
        print(f"[DRY RUN] Plan for: {task}")
        print(f"  Spec dir:    {spec_dir}/")
        print("  Pipeline:    Intent → Spec → Review → Plan → Implement → Verify → Fix → Report")
        configured_fix_cycles = (
            max_fix_cycles
            if max_fix_cycles is not None
            else cfg.get("plan", {}).get("max_fix_cycles", 2)
        )
        print(f"  Max fix cycles: {configured_fix_cycles}")
        return 0

    # Build one aggregate summary for the complete multi-agent pipeline.
    step_summaries = [step.summary for step in result.results]
    changed_files = list(dict.fromkeys(
        path
        for step_summary in step_summaries
        for path in step_summary.files_changed
    ))
    for artifact in (
        result.spec_path,
        result.plan_path,
        result.compliance_path,
        result.report_path,
    ):
        if artifact and artifact.exists() and str(artifact) not in changed_files:
            changed_files.append(str(artifact))
    started = [item.started_at for item in step_summaries if item.started_at]
    finished = [item.finished_at for item in step_summaries if item.finished_at]
    summary = RunSummary(
        run_id=step_summaries[0].run_id if step_summaries else "",
        status="success" if result.passed else "failed",
        exit_code=0 if result.passed else result.exit_code,
        task=task,
        directory=str(Path(directory).expanduser().resolve()),
        model=model or cfg["llm"]["model"],
        total_steps=sum(item.total_steps for item in step_summaries),
        final_message=(
            f"Plan {'PASSED' if result.passed else 'FAILED'} "
            f"after {result.iterations} iteration(s) "
            f"and {result.fix_cycles} fix cycle(s)."
        ),
        files_changed=changed_files,
        started_at=min(started) if started else None,
        finished_at=max(finished) if finished else None,
    )

    if result.results:
        save_summary(summary, result.results[-1].output_dir)

    if result.error:
        if json_output:
            print(format_json_report(summary, cfg))
        else:
            print(f"\n{'═' * 60}", file=sys.stderr)
            print("  PLAN FAILED", file=sys.stderr)
            print(f"{'═' * 60}", file=sys.stderr)
            print(f"\n{result.error}", file=sys.stderr)
            print("\n  Output: konstruktor-output/", file=sys.stderr)
        return result.exit_code

    if json_output:
        print(format_json_report(summary, cfg))
    else:
        print(format_text_report(summary, cfg))
        print(f"\n  Iterations:  {result.iterations}")
        print(f"  Fix cycles:  {result.fix_cycles}")
        print(f"  Spec:        {result.spec_path}")
        if result.plan_path:
            print(f"  Plan:        {result.plan_path}")
        if result.compliance_path:
            print(f"  Compliance:  {result.compliance_path}")
        if result.report_path:
            print(f"  Report:      {result.report_path}")
        if result.report_content:
            print(f"\n{'─' * 60}")
            print(result.report_content)
            print(f"{'─' * 60}")

    return 0 if result.passed else 1
