"""Eval runner — backend edit-block parser.

Runs every `parse` case through the BACKEND parser and compares against
`expect.edits`. `expect.normalized` is ignored here: it names a stage that
exists only in the client (`normalizeProposals`), so the TS runner owns it.

There is no skip list. Every parse case is in scope for this runner, so a case
cannot quietly stop being checked on one side.

Deliberately plain: no pytest, no fixtures, no Docker, because it has to run
identically from scripts/eval.sh and from a developer's shell. The scoring it
shares with run_gate.py lives in evals/harness.py and is unit-tested in
tests/test_eval_runner.py.
"""
import argparse
import sys
from pathlib import Path

from evals.harness import (
    BASELINE_PATH,
    CASES_DIR,
    check_corpus,
    load_baseline,
    load_cases,
    report,
)
from skills.legal_research.edit_parsing import _extract_proposed_edits


def run_case(case: dict) -> bool:
    prose = case["input"]["prose"]
    return _extract_proposed_edits(prose) == case["expect"]["edits"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backend-side parse cases.")
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    all_cases = load_cases(args.cases)
    if check_corpus(all_cases):
        return 1

    cases = [c for c in all_cases if c.get("kind") == "parse"]
    baseline = load_baseline(args.baseline)
    return report("parse-py", [(c["id"], run_case(c)) for c in cases], baseline)


if __name__ == "__main__":
    sys.exit(main())
