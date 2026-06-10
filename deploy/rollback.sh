#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  Codewalk Server Rollback Script
#  Run manually on the Hetzner server to rollback to previous SHA.
# ══════════════════════════════════════════════════════════════════════
set -e

STATE_FILE="/opt/codewalk/.deploy-state"
IMAGE="ghcr.io/gupta29470/codewalk:latest"

if [ ! -f "$STATE_FILE" ]; then
    echo "❌ No deploy state found at $STATE_FILE"
    echo "   Cannot determine previous SHA to rollback to."
    exit 1
fi

# Get previous SHA (second-to-last line, or last if only one)
CURRENT_SHA=$(tail -1 "$STATE_FILE")
PREV_SHA=$(tail -2 "$STATE_FILE" | head -1)

if [ -z "$PREV_SHA" ] || [ "$PREV_SHA" = "$CURRENT_SHA" ]; then
    echo "❌ No previous SHA available for rollback."
    echo "   Current: $CURRENT_SHA"
    exit 1
fi

echo "=== Codewalk Rollback ==="
echo "Current SHA:  $CURRENT_SHA"
echo "Rollback to:  $PREV_SHA"
echo ""

# Pull previous image
echo "Pulling previous image: $IMAGE:sha-$PREV_SHA"
docker pull "$IMAGE:sha-$PREV_SHA"
docker tag "$IMAGE:sha-$PREV_SHA" "$IMAGE:latest"

# Restart containers
cd /opt/codewalk
echo "Restarting containers..."
docker compose up -d --force-recreate

# Health check
echo "Running health check..."
HEALTHY=false
for i in {1..12}; do
    sleep 5
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health || echo "000")
    if [ "$STATUS" = "200" ]; then
        BODY=$(curl -s http://localhost:8000/health)
        if echo "$BODY" | grep -q '"status":"ok"'; then
            HEALTHY=true
            echo "✅ Health check passed ($i/12)"
            break
        fi
    fi
    echo "  Attempt $i/12: HTTP $STATUS, retrying..."
done

if [ "$HEALTHY" != "true" ]; then
    echo "❌ Rollback health check failed."
    exit 1
fi

# Update state file — remove current SHA, keep previous as current
head -n -1 "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"

echo ""
echo "✅ Rollback successful. Now running SHA: $PREV_SHA"
