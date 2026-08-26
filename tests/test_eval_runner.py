"""Unit tests for the eval runner's own logic.

The corpus itself is data and is not unit-tested — these cover only the parts
of the runner that could be wrong in a way no case would reveal: loading,
baseline diffing, and the score comparison that gates the push.
"""
import json
from pathlib import Path

from evals.harness import check_corpus, load_baseline, load_cases, score
from evals.run_gate import check_gate_cases
from evals.run_gate import run_case as run_gate_case


def test_load_cases_reads_every_json_sorted(tmp_path: Path):
    (tmp_path / "b.json").write_text(json.dumps({"id": "b", "kind": "parse"}))
    (tmp_path / "a.json").write_text(json.dumps({"id": "a", "kind": "parse"}))
    assert [c["id"] for c in load_cases(tmp_path)] == ["a", "b"]


def test_load_cases_defaults_id_to_filename(tmp_path: Path):
    (tmp_path / "parse-thing.json").write_text(json.dumps({"kind": "parse"}))
    assert load_cases(tmp_path)[0]["id"] == "parse-thing"


def test_load_baseline_missing_file_is_empty(tmp_path: Path):
    assert load_baseline(tmp_path / "nope.json") == {}


def test_score_counts_a_known_failure_as_expected():
    s = score([("a", True), ("b", False)], {"b": "known"})
    assert s["passed"] == 1 and s["expected"] == 1
    assert s["regressions"] == []


def test_score_reports_a_regression():
    s = score([("a", False)], {})
    assert s["regressions"] == ["a"]
    assert s["passed"] < s["expected"]


def test_score_reports_an_unexpected_pass():
    """A baselined case that starts passing needs a human as loudly as a regression."""
    s = score([("a", True)], {"a": "known"})
    assert s["unexpected_passes"] == ["a"]


def test_score_ignores_baseline_entries_for_absent_cases():
    """A stale baseline id must not silently lower the bar for the cases that ran."""
    s = score([("a", True)], {"gone": "case was deleted"})
    assert s["expected"] == 1


def test_corpus_check_rejects_a_kind_no_runner_owns():
    """A case naming an unknown kind runs in NO kind, so it would pass silently."""
    assert check_corpus([{"id": "x", "kind": "typo"}]) == 1


def test_corpus_check_accepts_a_backend_only_kind():
    """`gate` is owned by run_gate.py, not the TS runner. Owned-elsewhere is not unknown."""
    assert check_corpus([{"id": "x", "kind": "gate"}]) == 0


def test_a_rejection_case_without_a_reason_is_refused():
    """Without expect.error_contains a rejection case passes on ANY error, including
    one caused by a typo in the case itself — a case that tests nothing while looking
    green. The guard is what keeps the corpus from acquiring vacuous entries."""
    lax = {"id": "x", "expect": {"valid": False}}
    assert check_gate_cases([lax]) == 1
    lax["expect"]["error_contains"] = "not a complete sentence"
    assert check_gate_cases([lax]) == 0
    assert check_gate_cases([{"id": "y", "expect": {"valid": True}}]) == 0


def test_gate_case_rejection_must_match_the_stated_reason():
    """Rejected for the WRONG reason is a failed case, not a passed one — otherwise a
    fabrication case could pass because its row id was mistyped."""
    row = {"id": 1, "role": "user", "content": "We will not accept 12 months."}
    case = {
        "input": {"rows": [row], "body": '[#1 attorney] "accept 12 months"',
                  "from_id": 1, "to_id": 1},
        "expect": {"valid": False, "error_contains": "not a complete sentence"},
    }
    assert run_gate_case(case) is True
    case["expect"]["error_contains"] = "outside the range"
    assert run_gate_case(case) is False
