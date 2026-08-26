"""Shared scoring machinery for the backend eval runners.

Extracted so `run_parse.py` and `run_gate.py` cannot drift on the one thing
that must be identical across every runner: what "the score did not decrease"
means. The gate is score-must-not-decrease, never all-pass — a case promoted
from a real failure is failing by construction, and a binary gate would break
the push gate on every promotion until someone fixed the bug, so nobody would
ever promote one.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "evals" / "cases"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline.json"

# Every kind either side recognizes. A case whose "kind" isn't in here never
# matches any runner's filter, so it would run nowhere and pass silently.
# `gate` is backend-only by nature (it exercises Python-side compaction), and
# the TS runner names it explicitly for the same reason — "owned elsewhere" and
# "unrecognized" must not look alike to a corpus check.
KINDS = {"parse", "match", "apply", "gate"}


def load_cases(cases_dir: Path) -> list[dict]:
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        case = json.loads(path.read_text())
        case.setdefault("id", path.stem)
        cases.append(case)
    return cases


def load_baseline(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def score(results: list[tuple[str, bool]], baseline: dict[str, str]) -> dict:
    ran = {case_id for case_id, _ in results}
    # Only baseline entries whose case actually ran may lower the bar. A stale
    # id for a deleted case would otherwise silently forgive a live failure.
    known_failing = {cid for cid in baseline if cid in ran}
    passed = sum(1 for _, ok in results if ok)
    return {
        "passed": passed,
        "total": len(results),
        "expected": len(results) - len(known_failing),
        "regressions": [cid for cid, ok in results if not ok and cid not in known_failing],
        "unexpected_passes": [cid for cid, ok in results if ok and cid in known_failing],
    }


def report(label: str, results: list[tuple[str, bool]], baseline: dict[str, str]) -> int:
    """Print one score line and return the process exit code for this kind."""
    s = score(results, baseline)
    for case_id, ok in results:
        if not ok:
            marker = "known" if case_id in baseline else "FAIL"
            print(f"  [{marker}] {case_id}")
    for case_id in s["unexpected_passes"]:
        print(f"  [now-passing] {case_id} — in baseline but passed; remove the entry or check the case")
    known = s["total"] - s["expected"]
    suffix = f"   ({known} known-failing)" if known else ""
    print(f"{label} {s['passed']}/{s['total']}{suffix}")
    return 0 if not s["regressions"] and not s["unexpected_passes"] else 1


def check_corpus(all_cases: list[dict]) -> int:
    """0 if every case names a kind some runner will actually run, else 1.

    Called by every backend runner rather than just the first one: relying on
    run order would mean a corpus check that silently stops happening the day
    someone reorders scripts/eval.sh.
    """
    unknown = [c["id"] for c in all_cases if c.get("kind") not in KINDS]
    if unknown:
        print(f"  [corpus] unknown kind in: {', '.join(unknown)}")
        return 1
    return 0
