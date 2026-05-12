#!/bin/bash
# scripts/start.sh — start both FastAPI backend and Chainlit frontend
# Run from project root

set -e

echo "=== Starting Legal Plugin ==="

# Check Docker services
echo "Checking Docker services..."
docker compose ps --format "{{.Name}}: {{.Status}}" | head -7

# Start FastAPI backend
echo ""
echo "Starting FastAPI backend on port 8000..."
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# Wait for backend to be ready
sleep 3
curl -s http://localhost:8000/health > /dev/null 2>&1 || { echo "Backend failed to start"; kill $BACKEND_PID; exit 1; }
echo "Backend ready."

# Start Chainlit frontend
echo ""
echo "Starting Chainlit frontend on port 8080..."
chainlit run frontend/app.py --port 8080 --host 0.0.0.0 &
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
