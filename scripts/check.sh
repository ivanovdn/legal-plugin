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
for f in src/*.test.ts; do
  echo "--- $f"
  npx tsx "$f"
done

echo "==> all checks passed"
