#!/usr/bin/env bash
set -euo pipefail

# Smoke test for the non-CLI HTTP service.
# Usage:
#   bash examples/non_cli_http_smoke.sh
#   BASE_URL=http://127.0.0.1:8000 bash examples/non_cli_http_smoke.sh
#   MESSAGE="Summarize this repo" bash examples/non_cli_http_smoke.sh

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
THREAD_ID="${THREAD_ID:-smoke-thread-001}"
MESSAGE="${MESSAGE:-Please summarize the project in 3 bullets.}"

echo "==> Health check: ${BASE_URL}/health"
curl -sS "${BASE_URL}/health" | cat
echo

echo "==> Chat request: ${BASE_URL}/chat"
payload="$(cat <<JSON
{
  "message": "${MESSAGE}",
  "thread_id": "${THREAD_ID}"
}
JSON
)"

response="$(curl -sS -X POST "${BASE_URL}/chat" \
  -H "Content-Type: application/json" \
  -d "${payload}")"

if command -v jq >/dev/null 2>&1; then
  echo "${response}" | jq .
else
  echo "${response}"
fi

echo
echo "Smoke test finished."

