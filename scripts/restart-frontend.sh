#!/usr/bin/env bash
# Restart the Next.js frontend dev server.
# Kills any process listening on port 3000, clears the Next.js build cache,
# and starts the dev server fresh. Use this whenever you see stale chunk 404s
# or client-side exceptions after frontend code changes.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[restart-frontend] Killing process on port 3000 (if any)..."
if command -v lsof >/dev/null 2>&1; then
  lsof -ti:3000 | xargs kill -9 2>/dev/null || true
elif command -v fuser >/dev/null 2>&1; then
  fuser -k 3000/tcp 2>/dev/null || true
else
  echo "[restart-frontend] Warning: neither lsof nor fuser found; please free port 3000 manually."
fi

cd frontend
echo "[restart-frontend] Clearing Next.js build cache..."
rm -rf .next

echo "[restart-frontend] Starting Next.js dev server..."
npm run dev
