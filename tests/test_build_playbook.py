# tests/test_build_playbook.py
"""Tests for scripts/build_playbook.py.

The script is the seam between hand-authored legal materials and the in-prompt
bundle. We verify it produces the expected layout, that key tables/sections
land in the expected files, and that re-running on the same inputs is byte-
identical (caching + audit + clean PR diffs).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK = ROOT / "skills" / "contract_review" / "playbook"
CROSSREF = ROOT / "docs" / "playbook_cross_reference.md"


def _run_build():
    """Invoke the build script via the same Python interpreter running tests."""
    return subprocess.run(
        [sys.executable, "scripts/build_playbook.py"],
        cwd=str(ROOT),
        check=True,
        capture_output=True,
        text=True,
    )


def test_build_playbook_creates_global_files():
    """All 10 global/*.md files exist after the script runs."""
    _run_build()
    expected = {
        "role_and_golden_rules.md",
        "core_principles.md",
        "risk_rating.md",
        "approval_matrix.md",
        "output_format.md",
        "ai_review_procedure.md",
        "external_comments.md",
        "contract_selection.md",
        "clause_bank.md",
        "no_signature_checklist.md",
    }
    actual = {p.name for p in (PLAYBOOK / "global").glob("*.md")}
    assert expected.issubset(actual), f"missing: {expected - actual}"


def test_build_playbook_creates_per_type_folders():
    """nda/msa/sow/baa each get SKILL.md + playbook_matrix.md."""
    _run_build()
    for t in ("nda", "msa", "sow", "baa"):
        assert (PLAYBOOK / t / "SKILL.md").exists(), f"{t}/SKILL.md missing"
        assert (PLAYBOOK / t / "playbook_matrix.md").exists(), f"{t}/playbook_matrix.md missing"


@pytest.mark.parametrize(
    "ctype,marker",
    [("nda", "NDA-001"), ("msa", "MSA-001"), ("sow", "SOW-001"), ("baa", "BAA-001")],
)
def test_playbook_matrix_contains_first_row_id(ctype: str, marker: str):
    """Each per-type matrix must include its first canonical clause-bank ID."""
    _run_build()
    text = (PLAYBOOK / ctype / "playbook_matrix.md").read_text(encoding="utf-8")
    assert marker in text


def test_risk_rating_file_has_both_tables():
    """global/risk_rating.md merges Tables 1 + 2 (ratings + escalation owners)."""
    _run_build()
    text = (PLAYBOOK / "global" / "risk_rating.md").read_text(encoding="utf-8")
    assert "## Risk Ratings" in text
    assert "## Escalation Owners" in text
    assert "Missing Context" in text  # rating vocabulary
    assert "CLCO" in text  # escalation owner


def test_no_signature_checklist_canonical_from_references():
    """The gate file ships the team's checklist verbatim, including the
    certification language for both signing-may-proceed and blocker paths."""
    _run_build()
    text = (PLAYBOOK / "global" / "no_signature_checklist.md").read_text(encoding="utf-8")
    assert "DO NOT SEND FOR SIGNATURE" in text
    assert "Signature may proceed" in text


def test_external_comments_combines_rules_and_examples():
    """external_comments.md = rules (from references) + examples (playbook Table 10)."""
    _run_build()
    text = (PLAYBOOK / "global" / "external_comments.md").read_text(encoding="utf-8")
    assert "## Rules" in text
    assert "## Examples" in text


def test_ai_review_procedure_drops_competing_output_schemas():
    """Option A: keep §10.1 (behavior rules); drop §10.2/10.3/10.4 'AI output schema'
    blocks, which conflict with the canonical output_format.md. The bundle must carry
    a single output spec. See docs/output_format_conflict.md."""
    _run_build()
    text = (PLAYBOOK / "global" / "ai_review_procedure.md").read_text(encoding="utf-8")
    assert "Mandatory AI rules" in text       # §10.1 kept
    assert "AI output schema" not in text     # §10.2/10.3/10.4 headings dropped
    assert "Top 5 legal risks" not in text    # §10.3 body gone
    assert "SOW readiness" not in text        # §10.4 body gone


@pytest.mark.parametrize("ctype", ["nda", "msa", "sow", "baa"])
def test_per_type_skill_has_no_dangling_reference_paths(ctype: str):
    """The LLM has no file access; per-type SKILL.md must not tell it to open
    `references/*.md`. The build rewrites those pointers to inline phrasing while
    preserving intent. See docs/output_format_conflict.md."""
    _run_build()
    text = (PLAYBOOK / ctype / "SKILL.md").read_text(encoding="utf-8")
    assert "references/" not in text, f"{ctype}: dangling references/ path survived"
    # intent preserved
    assert "risk ratings" in text
    assert "No Signature Checklist" in text
    assert "required final output format" in text


def test_cross_reference_doc_exists_and_lists_canonical_sources():
    _run_build()
    text = CROSSREF.read_text(encoding="utf-8")
    assert "# Playbook Cross-Reference" in text
    # The canonical column should be visible somewhere
    assert "Canonical" in text
    assert "references" in text and "playbook" in text


def test_build_is_idempotent():
    """Re-running the script on the same inputs produces byte-identical files."""
    _run_build()
    snapshot = {
        p: p.read_bytes()
        for p in list(PLAYBOOK.rglob("*.md")) + [CROSSREF]
    }
    _run_build()
    for p, original in snapshot.items():
        assert p.read_bytes() == original, f"{p} changed on second build"
