#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  Codewalk Server Rollback Script
#  Uses 2-line state file: line 1 = previous, line 2 = current.
#  After rollback, swaps them so you can roll forward again.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

STATE_FILE="/opt/codewalk/.deploy-state"
IMAGE="ghcr.io/gupta29470/codewalk"
COMPOSE_DIR="/opt/codewalk"
SRC_DIR="/opt/codewalk-src"

if [ ! -f "$STATE_FILE" ]; then
    echo "❌ No deploy state at $STATE_FILE" >&2
    exit 1
fi

PREVIOUS_SHA=$(sed -n '1p' "$STATE_FILE" 2>/dev/null || echo "")
CURRENT_SHA=$(sed -n '2p' "$STATE_FILE" 2>/dev/null || echo "")

if [ -z "$PREVIOUS_SHA" ]; then
    echo "❌ No previous SHA available for rollback."
    echo "   Current SHA: $CURRENT_SHA"
    exit 1
fi

echo "=== Codewalk Rollback ==="
echo "Current SHA:  $CURRENT_SHA"
echo "Rollback to:  $PREVIOUS_SHA"
echo ""

# ── Get rollback image ──────────────────────────────────────────────
if docker pull "$IMAGE:sha-$PREVIOUS_SHA" 2>/dev/null; then
    docker tag "$IMAGE:sha-$PREVIOUS_SHA" "$IMAGE:latest"
    echo "✅ Pulled GHCR image"
elif [ -d "$SRC_DIR/.git" ]; then
    echo "Building rollback SHA locally..."
    cd "$SRC_DIR"
    git checkout "$PREVIOUS_SHA" 2>/dev/null || true
    docker build -f deploy/Dockerfile -t "$IMAGE:latest" .
    echo "✅ Local build complete"
else
    echo "❌ Cannot get rollback image (no GHCR, no source)" >&2
    exit 1
fi

# ── Restart ─────────────────────────────────────────────────────────
cd "$COMPOSE_DIR"
echo "Restarting containers..."
docker compose up -d --force-recreate

# ── Health check ────────────────────────────────────────────────────
echo ""
echo "Running health check..."
HEALTHY=false
for i in $(seq 1 12); do
    sleep 5
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        BODY=$(curl -s http://localhost:8000/health 2>/dev/null || echo "")
        if echo "$BODY" | grep -q '"status":"ok"'; then
            HEALTHY=true
            echo "✅ Health check passed ($i/12)"
            break
        fi
    fi
    echo "  Attempt $i/12: HTTP $STATUS, retrying..."
done

if [ "$HEALTHY" != "true" ]; then
    echo "❌ Rollback health check failed." >&2
    exit 1
fi

# ── Update state (swap current ↔ previous) ──────────────────────────
printf '%s\n%s\n' "$CURRENT_SHA" "$PREVIOUS_SHA" > "$STATE_FILE"

echo ""
echo "┌────────────────────────────────────────┐"
echo "│     ✅ ROLLBACK SUCCESSFUL             │"
echo "├────────────────────────────────────────┤"
printf "│  Now running:  %-24s│\n" "$PREVIOUS_SHA"
printf "│  Can roll to:  %-24s│\n" "$CURRENT_SHA"
echo "└────────────────────────────────────────┘"
