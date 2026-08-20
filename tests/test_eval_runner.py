"""Unit tests for the eval runner's own logic.

The corpus itself is data and is not unit-tested — these cover only the parts
of the runner that could be wrong in a way no case would reveal: loading,
baseline diffing, and the score comparison that gates the push.
"""
import json
from pathlib import Path

from evals.run_parse import load_baseline, load_cases, score


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
