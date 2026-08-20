"""Eval runner — backend half.

Runs every `parse` case through the BACKEND parser and compares against
`expect.edits`. `expect.normalized` is ignored here: it names a stage that
exists only in the client (`normalizeProposals`), so the TS runner owns it.

There is no skip list. Every parse case is in scope for this runner, so a case
cannot quietly stop being checked on one side.

Deliberately plain: no pytest, no fixtures, no Docker, because it has to run
identically from scripts/eval.sh and from a developer's shell. Its own logic is
unit-tested in tests/test_eval_runner.py.
"""
import argparse
import json
import sys
from pathlib import Path

from skills.legal_research.edit_parsing import _extract_proposed_edits

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "evals" / "cases"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline.json"

# The set of kinds either runner recognizes. A case whose "kind" isn't in here
# never matches any runner's `kind ==` filter, so it would otherwise run
# nowhere and pass silently — see the corpus check in main().
KINDS = {"parse", "match", "apply"}


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


def run_case(case: dict) -> bool:
    prose = case["input"]["prose"]
    return _extract_proposed_edits(prose) == case["expect"]["edits"]


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backend-side eval cases.")
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    all_cases = load_cases(args.cases)
    unknown = [c["id"] for c in all_cases if c.get("kind") not in KINDS]
    if unknown:
        print(f"  [corpus] unknown kind in: {', '.join(unknown)}")
        return 1

    cases = [c for c in all_cases if c.get("kind") == "parse"]
    baseline = load_baseline(args.baseline)
    results = [(c["id"], run_case(c)) for c in cases]
    s = score(results, baseline)

    for case_id, ok in results:
        if not ok:
            marker = "known" if case_id in baseline else "FAIL"
            print(f"  [{marker}] {case_id}")
    for case_id in s["unexpected_passes"]:
        print(f"  [now-passing] {case_id} — in baseline but passed; remove the entry or check the case")

    known = s["total"] - s["expected"]
    suffix = f"   ({known} known-failing)" if known else ""
    print(f"parse-py {s['passed']}/{s['total']}{suffix}")
    return 0 if not s["regressions"] and not s["unexpected_passes"] else 1


if __name__ == "__main__":
    sys.exit(main())
