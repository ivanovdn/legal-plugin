"""Eval runner — compaction validation gate.

The gate (`skills/legal_research/compaction.validate_segment`) decides whether a
quote may stand for the conversation row it cites. It is deterministic and
zero-LLM, so it evals exactly like a parser: feed it a (rows, body) pair and
assert the verdict.

Why a corpus and not only unit tests. This gate is a CLASSIFIER on a frontier,
and every change to it trades one column against the other — closing a
truncation shape costs legitimate quotes, and admitting a boundary buys them
back at the cost of a fabrication shape. A test suite reports that trade as
"you broke tests." A scored corpus reports it as what it is: N fabrications
rejected, M ordinary quotes still accepted. The corpus also owns the gate's
known defect, which a test suite structurally cannot hold — pinning a defect as
a passing test asserts the bug is correct, and makes FIXING it look like a
regression. Here it is a baselined failure, so a fix prints `[now-passing]` and
exits non-zero until someone acknowledges it.

Case shape:
    input:  {rows: [{id, role, content}], body: str, from_id: int, to_id: int}
    expect: {valid: bool, error_contains?: str}
`error_contains` is REQUIRED whenever `valid` is false. Without it a rejection
case passes on ANY rejection — including one caused by a typo'd row id in the
case itself — which is the vacuous-test failure mode this repo has already hit
three times.
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
from skills.legal_research.compaction import validate_segment


def run_case(case: dict) -> bool:
    spec = case["input"]
    err = validate_segment(spec["body"], spec["rows"], spec["from_id"], spec["to_id"])
    if case["expect"]["valid"]:
        return err == ""
    # A non-empty needle can never be found in "", so this also asserts rejection.
    return case["expect"]["error_contains"] in err


def check_gate_cases(cases: list[dict]) -> int:
    """0 if every rejection case says WHY it must be rejected, else 1."""
    lax = [
        c["id"] for c in cases
        if not c["expect"]["valid"] and not c["expect"].get("error_contains")
    ]
    if lax:
        print(
            "  [corpus] rejection case with no expect.error_contains "
            f"(would pass on any error): {', '.join(lax)}"
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run compaction-gate cases.")
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    all_cases = load_cases(args.cases)
    if check_corpus(all_cases):
        return 1

    cases = [c for c in all_cases if c.get("kind") == "gate"]
    if check_gate_cases(cases):
        return 1

    baseline = load_baseline(args.baseline)
    return report("gate-py", [(c["id"], run_case(c)) for c in cases], baseline)


if __name__ == "__main__":
    sys.exit(main())
