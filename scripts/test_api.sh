#!/bin/bash
# scripts/test_api.sh — verify API endpoints with curl
# Requires: API server running on port 8000, Docker services up

set -e
BASE="http://localhost:8000"

echo "=== 1. Health check ==="
curl -s "$BASE/health" | python3 -m json.tool

echo ""
echo "=== 2. Submit query ==="
curl -s -X POST "$BASE/api/query" \
  -H "Content-Type: application/json" \
  -H "X-User-ID: attorney-1" \
  -d '{"request": "What are indemnification standards in US contracts?"}' \
  | python3 -m json.tool

echo ""
echo "=== 3. Submit query with pre-set task_type ==="
curl -s -X POST "$BASE/api/query" \
  -H "Content-Type: application/json" \
  -H "X-User-ID: attorney-1" \
  -d '{"request": "Generate a service agreement", "task_type": "contract_generation"}' \
  | python3 -m json.tool

echo ""
echo "=== All API tests passed ==="
