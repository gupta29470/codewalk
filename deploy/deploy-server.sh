#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  Codewalk Server Deploy Script
#  Bounded image retention: keeps only latest + current + previous SHA.
#  Works from GitHub Actions (SHA via env) and manual runs.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────
IMAGE="ghcr.io/gupta29470/codewalk"
STATE_FILE="/opt/codewalk/.deploy-state"
LOG_FILE="/opt/codewalk/.deploy-log"
SRC_DIR="/opt/codewalk-src"
COMPOSE_DIR="/opt/codewalk"

# ── Get SHA ─────────────────────────────────────────────────────────
DEPLOY_SHA="${1:-${DEPLOY_SHA:-}}"

if [ -z "$DEPLOY_SHA" ] && [ -d "$SRC_DIR/.git" ]; then
    DEPLOY_SHA=$(cd "$SRC_DIR" && git rev-parse --short HEAD 2>/dev/null || true)
fi

if [ -z "$DEPLOY_SHA" ] && [ -f "$STATE_FILE" ]; then
    DEPLOY_SHA=$(sed -n '2p' "$STATE_FILE" 2>/dev/null || true)
fi

if [ -z "$DEPLOY_SHA" ]; then
    echo "❌ Cannot determine deploy SHA." >&2
    echo "   Provide as argument: $0 <sha>" >&2
    echo "   Or set DEPLOY_SHA env var." >&2
    echo "   Or ensure $SRC_DIR is a git repo." >&2
    exit 1
fi

DEPLOY_SHA=$(echo "$DEPLOY_SHA" | tr -cd 'a-f0-9' | head -c 12)

# ── Logging ─────────────────────────────────────────────────────────
exec >> "$LOG_FILE" 2>&1

echo ""
echo "=========================================="
echo "$(date -u +"%Y-%m-%d %H:%M:%S UTC") — Codewalk Deploy"
echo "SHA: $DEPLOY_SHA"
echo "=========================================="

# ── Read state (2-line format: prev, curr) ──────────────────────────
if [ -f "$STATE_FILE" ]; then
    PREVIOUS_SHA=$(sed -n '1p' "$STATE_FILE" 2>/dev/null || echo "")
    CURRENT_SHA=$(sed -n '2p' "$STATE_FILE" 2>/dev/null || echo "")
else
    PREVIOUS_SHA=""
    CURRENT_SHA=""
fi

echo "Previous SHA: ${PREVIOUS_SHA:-<none>}"
echo "Current SHA:  ${CURRENT_SHA:-<none>}"

# ── Prevent re-deploy ───────────────────────────────────────────────
if [ "$DEPLOY_SHA" = "$CURRENT_SHA" ]; then
    echo "⚠️  SHA $DEPLOY_SHA already deployed. Skipping."
    echo "   To force redeploy: rm $STATE_FILE && $0 $DEPLOY_SHA"
    exit 0
fi

# ── Try GHCR pull ───────────────────────────────────────────────────
GHCR_AVAILABLE=false
echo ""
echo "Attempting GHCR pull: $IMAGE:sha-$DEPLOY_SHA"

if docker pull "$IMAGE:sha-$DEPLOY_SHA" 2>/dev/null; then
    echo "✅ GHCR image pulled"
    docker tag "$IMAGE:sha-$DEPLOY_SHA" "$IMAGE:latest"
    GHCR_AVAILABLE=true
else
    echo "⚠️  GHCR pull failed — will build locally"
fi

# ── Local build fallback ────────────────────────────────────────────
if [ "$GHCR_AVAILABLE" != "true" ]; then
    if [ ! -d "$SRC_DIR/.git" ]; then
        echo "❌ No source at $SRC_DIR and GHCR unavailable. Cannot deploy." >&2
        exit 1
    fi

    echo ""
    echo "Building locally from $SRC_DIR ..."
    cd "$SRC_DIR"

    CURRENT_SRC_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "")
    if [ "$CURRENT_SRC_SHA" != "$DEPLOY_SHA" ]; then
        echo "Pulling latest code..."
        git fetch origin master
        git checkout "origin/master" 2>/dev/null || git pull origin master
    fi

    docker build -f deploy/Dockerfile -t "$IMAGE:latest" .
    echo "✅ Local build complete"
fi

# ── Sync deploy configs (.env stays manual) ─────────────────────────
echo ""
echo "Syncing docker-compose.yml and Caddyfile from $SRC_DIR ..."
if [ ! -f "$SRC_DIR/deploy/docker-compose.yml" ] || [ ! -f "$SRC_DIR/deploy/Caddyfile" ]; then
    echo "❌ Missing deploy configs in $SRC_DIR/deploy/" >&2
    exit 1
fi
cp "$SRC_DIR/deploy/docker-compose.yml" "$COMPOSE_DIR/docker-compose.yml"
cp "$SRC_DIR/deploy/Caddyfile" "$COMPOSE_DIR/Caddyfile"
echo "✅ Deploy configs synced"

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

# ── Rollback on failure ─────────────────────────────────────────────
if [ "$HEALTHY" != "true" ]; then
    echo ""
    echo "❌ Health check failed after 20 attempts"

    if [ -n "$CURRENT_SHA" ]; then
        echo "🔄 Rolling back to: $CURRENT_SHA"

        if docker pull "$IMAGE:sha-$CURRENT_SHA" 2>/dev/null; then
            docker tag "$IMAGE:sha-$CURRENT_SHA" "$IMAGE:latest"
        elif [ -d "$SRC_DIR/.git" ]; then
            echo "   Building rollback SHA locally..."
            cd "$SRC_DIR"
            git checkout "$CURRENT_SHA" 2>/dev/null || true
            docker build -f deploy/Dockerfile -t "$IMAGE:latest" .
        fi

        cd "$COMPOSE_DIR"
        docker compose up -d --force-recreate

        for i in $(seq 1 12); do
            sleep 5
            STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
            if [ "$STATUS" = "200" ]; then
                BODY=$(curl -s http://localhost:8000/health 2>/dev/null || echo "")
                if echo "$BODY" | grep -q '"status":"ok"'; then
                    echo "✅ Rollback successful"
                    echo "=========================================="
                    exit 1
                fi
            fi
            echo "  Rollback check $i/12: HTTP $STATUS, retrying..."
        done

        echo "❌❌ Rollback also failed. Manual intervention required." >&2
        exit 1
    else
        echo "❌ No previous SHA for rollback." >&2
        exit 1
    fi
fi

# ══════════════════════════════════════════════════════════════════════
#  SUCCESS PATH: Update state, clean up old images, print summary
# ══════════════════════════════════════════════════════════════════════

# ── Update state (2-line bounded format) ────────────────────────────
# Old current becomes previous; new SHA becomes current
printf '%s\n%s\n' "$CURRENT_SHA" "$DEPLOY_SHA" > "$STATE_FILE"

# ── Clean up old SHA-tagged images ──────────────────────────────────
echo ""
echo "Cleaning up old images..."
REMOVED_COUNT=0
KEPT_TAGS=""

while IFS= read -r tag; do
    [ -z "$tag" ] && continue
    # Extract SHA from tag: ghcr.io/...:sha-XXXXXXX
    TAG_SHA=$(echo "$tag" | sed 's/.*:sha-//')

    # Keep current and previous SHAs
    if [ "$TAG_SHA" = "$DEPLOY_SHA" ] || [ "$TAG_SHA" = "$CURRENT_SHA" ]; then
        KEPT_TAGS="$KEPT_TAGS  $tag\n"
        continue
    fi

    # Remove everything else
    if docker rmi "$tag" >/dev/null 2>&1; then
        echo "  🗑️  Removed: $tag"
        REMOVED_COUNT=$((REMOVED_COUNT + 1))
    else
        echo "  ⚠️  Skipped (in use): $tag"
    fi
done < <(docker images --format "{{.Repository}}:{{.Tag}}" | grep "^$IMAGE:sha-" || true)

# ── Prune dangling images ───────────────────────────────────────────
DANGLING=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l | tr -d ' ')
if [ "$DANGLING" -gt 0 ]; then
    docker image prune -f >/dev/null 2>&1 || true
    echo "  🗑️  Pruned $DANGLING dangling image(s)"
fi

# ── Build kept-tags list for summary ────────────────────────────────
KEPT_LIST="latest"
[ -n "$DEPLOY_SHA" ] && KEPT_LIST="$KEPT_LIST, sha-$DEPLOY_SHA"
[ -n "$CURRENT_SHA" ] && KEPT_LIST="$KEPT_LIST, sha-$CURRENT_SHA"

# ── Deployment Summary ──────────────────────────────────────────────
echo ""
echo "┌────────────────────────────────────────┐"
echo "│     ✅ DEPLOYMENT SUCCESSFUL           │"
echo "├────────────────────────────────────────┤"
printf "│  Current SHA:  %-24s│\n" "$DEPLOY_SHA"
printf "│  Previous SHA: %-24s│\n" "${CURRENT_SHA:-<none>}"
printf "│  Images kept:  %-24s│\n" "$KEPT_LIST"
printf "│  Images removed: %-22s│\n" "$REMOVED_COUNT"
echo "└────────────────────────────────────────┘"
echo ""
echo "=========================================="
