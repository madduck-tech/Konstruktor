"""Structural checks for the ADR directory, its index, and the default config copy.

The ADR process is described in `docs/adr/README.md`. This module makes the parts of that
process which are mechanically checkable actually hold: naming, numbering, required sections,
the status vocabulary, agreement between each ADR and the index, and agreement between the
annotated root copy of the default configuration and the packaged file that is loaded at
runtime.

It runs as part of the existing `pytest -v` command, which is already recorded in
`gopnik.json` under `stage1`.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = REPO_ROOT / "docs" / "adr"
INDEX_PATH = ADR_DIR / "README.md"
PACKAGED_CONFIG = REPO_ROOT / "src" / "konstruktor" / "config.default.yaml"
ROOT_CONFIG = REPO_ROOT / "config.default.yaml"
DOCS_WITH_ADR_LINKS = (REPO_ROOT / "README.md", REPO_ROOT / "AGENTS.md")

FILENAME_RE = re.compile(r"^(\d{4})-([a-z0-9]+(?:-[a-z0-9]+)*)\.md$")
HEADER_RE = re.compile(r"^# ADR-(\d{4}): (.+)$", re.MULTILINE)
STATUS_RE = re.compile(r"^- \*\*Status:\*\* (.+)$", re.MULTILINE)
DATE_RE = re.compile(r"^- \*\*Date:\*\* (.+)$", re.MULTILINE)
SUPERSEDES_RE = re.compile(r"^Superseded by ADR-(\d{4})$")
INDEX_ROW_RE = re.compile(
    r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|$",
    re.MULTILINE,
)
ADR_LINK_RE = re.compile(r"\]\((docs/adr/[^)]+)\)")

REQUIRED_SECTIONS = (
    "## Context",
    "## Decision",
    "## Alternatives considered",
    "## Consequences",
    "## Rationale provenance",
)
PLAIN_STATUSES = {"Proposed", "Accepted", "Deprecated", "Rejected"}


class Adr:
    """One ADR file parsed into the fields the checks care about."""

    def __init__(self, path: Path):
        self.path = path
        self.text = path.read_text(encoding="utf-8")

        match = FILENAME_RE.match(path.name)
        assert match, f"{path.name} does not match NNNN-kebab-title.md"
        self.number = match.group(1)
        self.slug = match.group(2)

        header = HEADER_RE.search(self.text)
        assert header, f"{path.name} has no '# ADR-NNNN: <Title>' heading"
        self.heading_number = header.group(1)
        self.title = header.group(2).strip()

        status = STATUS_RE.search(self.text)
        assert status, f"{path.name} has no '**Status:**' field"
        self.status = status.group(1).strip()

        dated = DATE_RE.search(self.text)
        self.date_value = dated.group(1).strip() if dated else ""

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def supersedes(self) -> str | None:
        """The ADR number this one supersedes, if it declares one."""
        match = SUPERSEDES_RE.match(self.status)
        return match.group(1) if match else None

    @property
    def is_known_status(self) -> bool:
        return self.status in PLAIN_STATUSES or self.supersedes is not None


def _load_adrs() -> dict[str, Adr]:
    """Parse every ADR file in the directory, keyed by number."""
    adrs: dict[str, Adr] = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        if path.name == INDEX_PATH.name:
            continue
        adr = Adr(path)
        assert adr.number not in adrs, (
            f"duplicate ADR number {adr.number}: {adrs[adr.number].filename} and {adr.filename}"
        )
        adrs[adr.number] = adr
    return adrs


def _parse_index() -> dict[str, tuple[str, str, str, str]]:
    """Parse the index table in docs/adr/README.md into number -> (link, title, status, date)."""
    index_text = INDEX_PATH.read_text(encoding="utf-8")
    rows = INDEX_ROW_RE.findall(index_text)
    parsed: dict[str, tuple[str, str, str, str]] = {}
    for number, link, title, status, dated in rows:
        assert number not in parsed, f"index lists ADR {number} twice"
        parsed[number] = (link, title.strip(), status.strip(), dated.strip())
    return parsed


ADRS = _load_adrs()
INDEX = _parse_index()


def test_adr_directory_is_not_empty():
    assert ADRS, f"no ADR files found in {ADR_DIR}"


def test_adr_numbers_start_at_0001_and_are_contiguous():
    numbers = sorted(int(number) for number in ADRS)
    assert numbers == list(range(1, len(numbers) + 1))


def test_adr_heading_matches_filename():
    for adr in ADRS.values():
        assert adr.heading_number == adr.number, (
            f"{adr.filename} is titled 'ADR-{adr.heading_number}'"
        )


def test_adr_status_is_from_the_known_vocabulary():
    for adr in ADRS.values():
        assert adr.is_known_status, (
            f"{adr.filename} has status {adr.status!r}, which is not one of "
            f"{sorted(PLAIN_STATUSES)} or 'Superseded by ADR-NNNN'"
        )


def test_non_proposed_adrs_carry_a_valid_date():
    for adr in ADRS.values():
        if adr.status == "Proposed":
            continue
        assert adr.date_value, f"{adr.filename} is {adr.status} but has no Date"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", adr.date_value), (
            f"{adr.filename} date {adr.date_value!r} is not YYYY-MM-DD"
        )
        date.fromisoformat(adr.date_value)


def test_superseding_adr_points_at_an_existing_adr():
    for adr in ADRS.values():
        target = adr.supersedes
        if target is None:
            continue
        assert target in ADRS, f"{adr.filename} supersedes unknown ADR-{target}"


def test_adr_has_all_required_sections():
    for adr in ADRS.values():
        missing = [section for section in REQUIRED_SECTIONS if section not in adr.text]
        assert not missing, f"{adr.filename} is missing sections: {', '.join(missing)}"


def test_every_adr_appears_in_the_index_exactly_once():
    assert set(INDEX) == set(ADRS), (
        f"index and directory disagree: only in index {sorted(set(INDEX) - set(ADRS))}, "
        f"only on disk {sorted(set(ADRS) - set(INDEX))}"
    )


def test_index_links_resolve_and_match_the_adr_title_and_status():
    for number, (link, title, status, _dated) in INDEX.items():
        adr = ADRS[number]
        assert link == adr.filename, f"index links {number} to {link}, file is {adr.filename}"
        assert (ADR_DIR / link).exists(), f"index links to missing file {link}"
        assert title == adr.title, (
            f"index title for ADR-{number} is {title!r}, heading says {adr.title!r}"
        )
        assert status == adr.status, (
            f"index status for ADR-{number} is {status!r}, file says {adr.status!r}"
        )


def test_index_date_matches_the_adr_date():
    for number, (_link, _title, _status, dated) in INDEX.items():
        assert dated == ADRS[number].date_value, (
            f"index dates ADR-{number} as {dated!r}, file says {ADRS[number].date_value!r}"
        )


@pytest.mark.parametrize("doc_path", DOCS_WITH_ADR_LINKS, ids=lambda path: path.name)
def test_adr_links_in_docs_resolve(doc_path):
    text = doc_path.read_text(encoding="utf-8")
    for link in ADR_LINK_RE.findall(text):
        assert (REPO_ROOT / link).exists(), f"{doc_path.name} links to missing {link}"


def test_annotated_root_config_matches_the_packaged_defaults():
    """`src/konstruktor/config.default.yaml` is what the wheel loads (see ADR-0008).

    The root copy is documentation; a value that differs in either direction is a lie
    about what Konstruktor actually defaults to.
    """
    packaged = yaml.safe_load(PACKAGED_CONFIG.read_text(encoding="utf-8"))
    root = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8"))
    assert root == packaged, (
        "config.default.yaml and src/konstruktor/config.default.yaml disagree: "
        f"root-only keys {sorted(set(root) - set(packaged))}, "
        f"packaged-only keys {sorted(set(packaged) - set(root))}"
    )


def test_root_config_copy_declares_the_authoritative_file():
    header = ROOT_CONFIG.read_text(encoding="utf-8").split("llm:", 1)[0]
    assert "src/konstruktor/config.default.yaml" in header, (
        "the annotated root config must name the authoritative file it mirrors"
    )
