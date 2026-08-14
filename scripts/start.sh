#!/bin/bash
# scripts/start.sh — start both FastAPI backend and Chainlit frontend
# Run from project root

set -e

echo "=== Starting Legal Plugin ==="

BACKEND_PORT=8000

# Check Docker services. NO `head -N` — truncating the list hides whichever
# service sorts last and sends you hunting a phantom outage (redis looked
# "missing" this way while being up and healthy on line 8).
echo "Checking Docker services..."
docker compose ps --format "{{.Name}}: {{.Status}}"

# Pre-flight: is the backend port already taken?
#
# uvicorn binds *:8000 and starts happily even when another process holds the
# MORE SPECIFIC 127.0.0.1:8000 — macOS then routes every localhost connection
# to that other listener instead of ours. VS Code's automatic port forwarding
# does exactly this, and a stale forward ACCEPTS the connection and never
# answers, so a perfectly healthy backend presents as hung.
# We have not started anything yet, so ANY listener here belongs to someone
# else — a stale backend, or a forwarder. Either way localhost:8000 will not
# reliably reach the backend we are about to launch, so stop now rather than
# spend the health-check budget discovering it.
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo ""
  echo "Port $BACKEND_PORT is already taken — refusing to start:"
  lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN | tail -n +2 | sed 's/^/  /'
  echo ""
  echo "  Nothing of ours is running yet, so that listener is not our backend."
  echo "  A stale run:  kill the PID above."
  echo "  VS Code:      it auto-forwards ports and keeps the socket bound after"
  echo "                the target dies, accepting connections and never replying."
  echo "                Set \"remote.autoForwardPorts\": false, then reload the window."
  exit 1
fi

# Start FastAPI backend
echo ""
echo "Starting FastAPI backend on port $BACKEND_PORT..."
source .venv/bin/activate
# Bind the LOOPBACK ADDRESS SPECIFICALLY, not the 0.0.0.0 wildcard.
#
# This is what stops VS Code stealing the port. A wildcard bind leaves
# 127.0.0.1:8000 unclaimed, and macOS routes localhost connections to the more
# specific listener — so VS Code's port auto-forwarding grabs it (repeatedly:
# five different helper PIDs in one afternoon) and then accepts connections
# without ever answering. Binding 127.0.0.1 takes the address, and VS Code
# physically cannot have it. Vite has never had this problem for exactly this
# reason — vite.config.ts already pins host: "127.0.0.1".
#
# Nothing local needs the wildcard: Word reaches the backend through Vite's
# proxy and Chainlit is on the same machine. The CONTAINER is unaffected — the
# Dockerfile has its own CMD with --host 0.0.0.0, which it needs for Docker
# networking.
uvicorn api.main:app --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# Wait for the backend, with a TIMEOUT and a bounded retry.
#
# The old check was a bare `curl -s`. With no -m, curl blocks forever against a
# listener that accepts and never responds, so the script stopped here in
# silence: no "Backend ready.", no Chainlit, and uvicorn's own startup lines
# still on screen — indistinguishable from a crashed backend.
echo "Waiting for backend..."
health_code=""
for _ in $(seq 1 12); do
  health_code=$(curl -s -m 2 -o /dev/null -w '%{http_code}' \
    "http://localhost:$BACKEND_PORT/health" 2>/dev/null || true)
  if [ "$health_code" = "200" ]; then break; fi
  # Distinguish "still booting" from "already dead" — no point retrying a corpse.
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Backend process exited during startup — see the traceback above."
    exit 1
  fi
  sleep 1
done

if [ "$health_code" != "200" ]; then
  echo ""
  echo "Backend did not answer on localhost:$BACKEND_PORT (last status: ${health_code:-none})."
  echo "Its process is still alive, so this is usually NOT a crash. Listeners now:"
  lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 | sed 's/^/  /'
  echo "  Two entries above means another process owns 127.0.0.1:$BACKEND_PORT"
  echo "  and is intercepting localhost traffic — free it, then retry."
  kill "$BACKEND_PID" 2>/dev/null || true
  exit 1
fi
echo "Backend ready."

# Start Chainlit frontend
echo ""
echo "Starting Chainlit frontend on port 8080..."
chainlit run clients/web/app.py --port 8080 --host 0.0.0.0 &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID"

echo ""
echo "=== Legal Plugin Running ==="
echo "  Backend:  http://localhost:8000 (API docs: http://localhost:8000/docs)"
echo "  Frontend: http://localhost:8080"
echo ""
echo "Press Ctrl+C to stop both services."

# Wait for either to exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
