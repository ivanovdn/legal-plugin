#!/usr/bin/env bash
# Deterministic eval harness (Tier 1). No LLM calls; safe to run anywhere.
#
# The gate is "the score must not decrease", never "all cases pass". A case
# promoted from a tester's complaint is currently-failing by construction, so a
# binary gate would mean every promotion breaks the push gate until someone
# fixes the bug — and nobody would ever promote one. Known failures live in
# evals/baseline.json with a reason, and are counted on every run.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> eval: backend parser"
uv run python -m evals.run_parse

echo "==> eval: client parser, matcher, apply"
(cd clients/word && npx tsx src/eval/runner.ts)

echo "==> eval: no regressions"
