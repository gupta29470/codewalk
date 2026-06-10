#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  Codewalk Server Deploy Script
#  Works both from GitHub Actions (with SHA env var) and manually.
#  Tries GHCR first, falls back to local build if unavailable.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────
IMAGE="ghcr.io/gupta29470/codewalk"
STATE_FILE="/opt/codewalk/.deploy-state"
LOG_FILE="/opt/codewalk/.deploy-log"
SRC_DIR="/opt/codewalk-src"
COMPOSE_DIR="/opt/codewalk"

# ── Get SHA ─────────────────────────────────────────────────────────
# Priority: 1. Argument, 2. DEPLOY_SHA env var, 3. Git in src dir, 4. Last known
DEPLOY_SHA="${1:-${DEPLOY_SHA:-}}"

if [ -z "$DEPLOY_SHA" ] && [ -d "$SRC_DIR/.git" ]; then
    DEPLOY_SHA=$(cd "$SRC_DIR" && git rev-parse --short HEAD 2>/dev/null || true)
fi

if [ -z "$DEPLOY_SHA" ] && [ -f "$STATE_FILE" ]; then
    DEPLOY_SHA=$(tail -1 "$STATE_FILE" 2>/dev/null || true)
fi

if [ -z "$DEPLOY_SHA" ]; then
    echo "❌ Cannot determine deploy SHA."
    echo "   Provide as argument: $0 <sha>"
    echo "   Or set DEPLOY_SHA env var."
    echo "   Or ensure $SRC_DIR is a git repo."
    exit 1
fi

# Sanitize SHA (only hex chars, max 12)
DEPLOY_SHA=$(echo "$DEPLOY_SHA" | tr -cd 'a-f0-9' | head -c 12)

# ── Logging ─────────────────────────────────────────────────────────
exec >> "$LOG_FILE" 2>&1
echo ""
echo "=========================================="
echo "$(date -u +"%Y-%m-%d %H:%M:%S UTC") — Codewalk Deploy"
echo "SHA: $DEPLOY_SHA"
echo "=========================================="

# ── Load previous SHA ───────────────────────────────────────────────
if [ -f "$STATE_FILE" ]; then
    PREV_SHA=$(tail -1 "$STATE_FILE" 2>/dev/null || echo "")
    # Get second-to-last for rollback
    ROLLBACK_SHA=$(tail -2 "$STATE_FILE" 2>/dev/null | head -1 || echo "")
else
    PREV_SHA=""
    ROLLBACK_SHA=""
fi

echo "Previous SHA: ${PREV_SHA:-<none>}"
echo "Rollback SHA: ${ROLLBACK_SHA:-<none>}"

# ── Prevent re-deploy of same SHA ──────────────────────────────────
if [ "$DEPLOY_SHA" = "$PREV_SHA" ]; then
    echo "⚠️ SHA $DEPLOY_SHA already deployed. Skipping."
    echo "   To force redeploy: rm $STATE_FILE && $0 $DEPLOY_SHA"
    exit 0
fi

# ── Try GHCR pull first ─────────────────────────────────────────────
GHCR_AVAILABLE=false
echo ""
echo "Attempting GHCR pull: $IMAGE:sha-$DEPLOY_SHA"

if docker pull "$IMAGE:sha-$DEPLOY_SHA" 2>/dev/null; then
    echo "✅ GHCR image pulled successfully"
    docker tag "$IMAGE:sha-$DEPLOY_SHA" "$IMAGE:latest"
    GHCR_AVAILABLE=true
else
    echo "⚠️ GHCR pull failed (image not found or not accessible)"
    echo "   Will build locally from source instead."
fi

# ── Fall back to local build ────────────────────────────────────────
if [ "$GHCR_AVAILABLE" != "true" ]; then
    if [ ! -d "$SRC_DIR/.git" ]; then
        echo "❌ No source code at $SRC_DIR and GHCR unavailable."
        echo "   Cannot deploy. Clone repo first:"
        echo "   git clone https://github.com/gupta29470/codewalk.git $SRC_DIR"
        exit 1
    fi

    echo ""
    echo "Building locally from $SRC_DIR ..."
    cd "$SRC_DIR"

    # Pull latest code if we're not on the target SHA
    CURRENT_SRC_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "")
    if [ "$CURRENT_SRC_SHA" != "$DEPLOY_SHA" ]; then
        echo "Pulling latest code..."
        git fetch origin master
        git checkout "origin/master" 2>/dev/null || git pull origin master
    fi

    docker build -f deploy/Dockerfile -t "$IMAGE:latest" .
    echo "✅ Local build complete"
fi

# ── Restart containers ──────────────────────────────────────────────
echo ""
echo "Restarting containers..."
cd "$COMPOSE_DIR"
docker compose up -d --force-recreate --remove-orphans

# ── Health check ────────────────────────────────────────────────────
echo ""
echo "Running health check..."
HEALTHY=false
for i in $(seq 1 20); do
    sleep 5
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
    if [ "$STATUS" = "200" ]; then
        BODY=$(curl -s http://localhost:8000/health 2>/dev/null || echo "")
        if echo "$BODY" | grep -q '"status":"ok"'; then
            HEALTHY=true
            echo "✅ Health check passed (attempt $i/20)"
            break
        fi
    fi
    echo "  Attempt $i/20: HTTP $STATUS, retrying..."
done

# ── Rollback if health failed ───────────────────────────────────────
if [ "$HEALTHY" != "true" ]; then
    echo ""
    echo "❌ Health check failed after 20 attempts (100s)"

    if [ -n "$ROLLBACK_SHA" ] && [ "$ROLLBACK_SHA" != "$DEPLOY_SHA" ]; then
        echo "🔄 Rolling back to previous SHA: $ROLLBACK_SHA"

        # Try GHCR rollback first
        if docker pull "$IMAGE:sha-$ROLLBACK_SHA" 2>/dev/null; then
            docker tag "$IMAGE:sha-$ROLLBACK_SHA" "$IMAGE:latest"
        elif [ -d "$SRC_DIR/.git" ]; then
            # Fall back to local build of rollback SHA
            echo "   Building rollback SHA locally..."
            cd "$SRC_DIR"
            git checkout "$ROLLBACK_SHA" 2>/dev/null || true
            docker build -f deploy/Dockerfile -t "$IMAGE:latest" .
        fi

        cd "$COMPOSE_DIR"
        docker compose up -d --force-recreate

        # Re-check health after rollback
        ROLLBACK_HEALTHY=false
        for i in $(seq 1 12); do
            sleep 5
            STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
            if [ "$STATUS" = "200" ]; then
                BODY=$(curl -s http://localhost:8000/health 2>/dev/null || echo "")
                if echo "$BODY" | grep -q '"status":"ok"'; then
                    ROLLBACK_HEALTHY=true
                    echo "✅ Rollback health check passed"
                    break
                fi
            fi
            echo "  Rollback check $i/12: HTTP $STATUS, retrying..."
        done

        if [ "$ROLLBACK_HEALTHY" = "true" ]; then
            echo "⚠️ Deployment rolled back to $ROLLBACK_SHA. Marking as failed."
            exit 1
        else
            echo "❌❌ Rollback also failed. Manual intervention required."
            echo "   Last known working SHA: $ROLLBACK_SHA"
            echo "   Failed SHA: $DEPLOY_SHA"
            exit 1
        fi
    else
        echo "❌ No previous SHA available for rollback."
        echo "   Current state is BROKEN. Manual fix required."
        exit 1
    fi
fi

# ── Save successful deploy ──────────────────────────────────────────
echo "$DEPLOY_SHA" >> "$STATE_FILE"
tail -10 "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"

# ── Cleanup ─────────────────────────────────────────────────────────
docker system prune -f 2>/dev/null || true

echo ""
echo "✅ Deploy successful. SHA: $DEPLOY_SHA"
echo "=========================================="
