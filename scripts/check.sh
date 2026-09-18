#!/usr/bin/env bash
# Pre-push gate: everything that can be checked without a live LLM.
#
# There is no CI on this project — the VM pulls from `ado` directly, so this
# is the only gate between a change and production. Run it before every push.
#
# Requires Docker (tests/conftest.py spins an ephemeral Postgres).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> backend tests"
uv run pytest tests/ -q

echo "==> degradation vocabulary"
uv run python scripts/check_degradation_vocabulary.py

echo "==> word add-in typecheck"
cd clients/word
npx tsc --noEmit

echo "==> word add-in assertions"
# EXPECTED_PASS_COUNT is the total PASS: line count across every src/*.test.ts,
# checked in one at the end. Without this, a test file that exits early (a bug
# skips its later assertions but still exits 0) or a new *.test.ts that doesn't
# match the glob would silently run fewer assertions than intended and still
# print "all checks passed" — exactly the failure mode this gate exists to catch.
EXPECTED_PASS_COUNT=291
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

echo "==> eval harness"
cd "$REPO_ROOT"
bash scripts/eval.sh

# The deployed artifact is NOT the dev venv, and until 2026-09-16 nothing here
# ever looked at it. `Dockerfile` installs requirements-runtime.txt, NOT
# requirements.txt, so a top-level import can resolve perfectly in .venv and be
# absent from the image. Two opentelemetry-instrumentation-* imports did
# exactly that: 27 commits, a review per task and a fully green run of THIS
# script, and the container still died at `uvicorn api.main:app` with
# ModuleNotFoundError before serving one request.
#
# Cost is bounded by layer caching: measured 0.40s fully cached and 0.44s when
# only source changed (the common case), because the pip layer is keyed on
# requirements-runtime.txt alone. It is only slow when a requirements file
# changed — which is precisely when this must run. Docker is already required
# above (testcontainers), so this adds no new dependency.
echo "==> backend image builds + app imports inside it"
image_log="$(mktemp)"
if ! docker build -t legal-plugin-backend:checkgate . >"$image_log" 2>&1; then
  cat "$image_log" >&2; rm -f "$image_log"
  echo "FAIL: the backend Docker image does not build." >&2
  exit 1
fi
if ! docker run --rm legal-plugin-backend:checkgate python -c "import api.main" >"$image_log" 2>&1; then
  cat "$image_log" >&2; rm -f "$image_log"
  echo "FAIL: the image builds, but 'import api.main' DIES INSIDE IT." >&2
  echo "      docker-compose.remote.yml builds this image for the VM, so this" >&2
  echo "      is a container that cannot start — production down, not degraded." >&2
  echo "      Almost always: a top-level import in a module reachable from" >&2
  echo "      api.main is declared in requirements.txt but NOT in" >&2
  echo "      requirements-runtime.txt, which is the only one the image" >&2
  echo "      installs. Declare it in BOTH. Do not 'verify' by importing in" >&2
  echo "      .venv — that is the dev environment, and it is what hid this" >&2
  echo "      for 27 commits (2026-09-16 opentelemetry-instrumentation-*)." >&2
  exit 1
fi
rm -f "$image_log"

echo "==> all checks passed"
