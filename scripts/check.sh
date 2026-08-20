#!/usr/bin/env bash
# Pre-push gate: everything that can be checked without a live LLM.
#
# There is no CI on this project — the VM pulls from `ado` directly, so this
# is the only gate between a change and production. Run it before every push.
#
# Requires Docker (tests/conftest.py spins an ephemeral Postgres).
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> backend tests"
uv run pytest tests/ -q

echo "==> word add-in typecheck"
cd clients/word
npx tsc --noEmit

echo "==> word add-in assertions"
# EXPECTED_PASS_COUNT is the total PASS: line count across every src/*.test.ts,
# checked in one at the end. Without this, a test file that exits early (a bug
# skips its later assertions but still exits 0) or a new *.test.ts that doesn't
# match the glob would silently run fewer assertions than intended and still
# print "all checks passed" — exactly the failure mode this gate exists to catch.
EXPECTED_PASS_COUNT=258
pass_log="$(mktemp)"
trap 'rm -f "$pass_log"' EXIT
for f in src/*.test.ts; do
  echo "--- $f"
  npx tsx "$f" | tee -a "$pass_log"
done

actual_pass_count=$(grep -c '^PASS: ' "$pass_log" || true)
if [ "$actual_pass_count" -ne "$EXPECTED_PASS_COUNT" ]; then
  echo "FAIL: expected $EXPECTED_PASS_COUNT total PASS assertions across src/*.test.ts, got $actual_pass_count" >&2
  echo "      (fix a regression, or if you added/removed assertions on purpose, update EXPECTED_PASS_COUNT above)" >&2
  exit 1
fi
echo "==> word add-in assertions: $actual_pass_count/$EXPECTED_PASS_COUNT PASS"

echo "==> all checks passed"
