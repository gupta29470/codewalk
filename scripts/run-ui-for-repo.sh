#!/usr/bin/env bash
# Launch the Codewalk frontend (from the Codewalk checkout) against a user repo.
#
# Usage:
#   From the target repo:
#     /path/to/codewalk/scripts/run-ui-for-repo.sh
#   Or with an explicit repo path:
#     /path/to/codewalk/scripts/run-ui-for-repo.sh /path/to/repo
#
# The backend is started from the user repo cwd so it discovers codewalk.yaml
# and loads .codewalk/ from that repo. The frontend is served from the Codewalk
# checkout, but its filesystem API routes read CODEWALK_REPO_PATH.
set -euo pipefail

REPO_PATH="${1:-$(pwd)}"
REPO_PATH="$(cd "$REPO_PATH" && pwd)"
CODEWALK_PATH="$(cd "$(dirname "$0")/.." && pwd)"

API_PORT="${CODEWALK_API_PORT:-8000}"
FRONTEND_PORT="${CODEWALK_FRONTEND_PORT:-3000}"

# Find a Python interpreter from the Codewalk checkout.
PYTHON_BIN="$CODEWALK_PATH/.codewalk-env/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$CODEWALK_PATH/.venv/bin/python"
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "[run-ui-for-repo] No .codewalk-env or .venv found; trying system python" >&2
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "[run-ui-for-repo] Error: could not find a Python interpreter." >&2
  exit 1
fi

# Load Codewalk's .env if present (LLM provider, keys, etc.).
if [ -f "$CODEWALK_PATH/.env" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$CODEWALK_PATH/.env"
  set +a
fi

export PYTHONPATH="$CODEWALK_PATH"
export CODEWALK_PATH="$CODEWALK_PATH"
export NEXT_PUBLIC_API_URL="http://localhost:$API_PORT"
export CODEWALK_REPO_PATH="$REPO_PATH"

cleanup() {
  echo "[run-ui-for-repo] Shutting down..."
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
  wait "$API_PID" "$UI_PID" 2>/dev/null || true
}

# Free the ports first.
echo "[run-ui-for-repo] Freeing ports $API_PORT and $FRONTEND_PORT..."
if command -v lsof >/dev/null 2>&1; then
  lsof -ti:"$API_PORT" | xargs kill -9 2>/dev/null || true
  lsof -ti:"$FRONTEND_PORT" | xargs kill -9 2>/dev/null || true
elif command -v fuser >/dev/null 2>&1; then
  fuser -k "$API_PORT/tcp" 2>/dev/null || true
  fuser -k "$FRONTEND_PORT/tcp" 2>/dev/null || true
fi

echo "[run-ui-for-repo] Repo:      $REPO_PATH"
echo "[run-ui-for-repo] Codewalk:  $CODEWALK_PATH"
echo "[run-ui-for-repo] API:       http://localhost:$API_PORT"
echo "[run-ui-for-repo] Frontend:  http://localhost:$FRONTEND_PORT"

# Start the backend from the user repo cwd.
cd "$REPO_PATH"
"$PYTHON_BIN" -m uvicorn src.codewalk.api.main:app --host 127.0.0.1 --port "$API_PORT" &
API_PID=$!

# Start the frontend from the Codewalk checkout.
cd "$CODEWALK_PATH/frontend"
npm run dev -- --port "$FRONTEND_PORT" &
UI_PID=$!

trap cleanup EXIT INT TERM

echo "[run-ui-for-repo] Both services starting. Press Ctrl-C to stop."
wait
