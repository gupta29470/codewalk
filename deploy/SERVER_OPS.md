# Codewalk Server Ops — Command Reference

> **Production:** `https://api.codewalk.xyz`  
> **Run on server:** SSH as root, then `cd /opt/codewalk` for all `docker compose` commands.  
> **Run from laptop:** Use `curl` against the public API (admin routes need `X-Admin-Key`).

---

## 0. One-time shell setup (laptop or server)

```bash
# Laptop — admin API
export API="https://api.codewalk.xyz"
export ADMIN_API_KEY="your-admin-key-from-opt-codewalk-env"

# Server — always work from compose dir
cd /opt/codewalk

# Optional — target one repo in commands below
export REPO="gupta29470/codewalk"
export OWNER="gupta29470"
export NAME="codewalk"
```

**Key paths on server:**

| Path | Purpose |
|------|---------|
| `/opt/codewalk` | `docker-compose.yml`, `.env`, `Caddyfile` — **run compose here** |
| `/opt/codewalk-src` | Git clone — `git pull` for source; **does not update running container** |
| `/var/codewalk/repos/{owner}/{repo}` | Cloned git repos |
| `/var/codewalk/indexes/{owner}/{repo}` | Active index (latest) — chroma, duckdb, manifest |
| `/var/codewalk/indexes/{owner}/{repo}.incoming.*` | Scratch builds; promoted via atomic swap (safe to delete if orphaned) |
| `/var/codewalk/secrets/` | GitHub App PEM (`chmod 600`, owner `999:999`) |
| `/opt/codewalk/.deploy-state` | Last deployed SHAs (2 lines: prev, curr) |
| `/opt/codewalk/.deploy-log` | Deploy script log |

**Container names** (default project `codewalk`):

- `codewalk-codewalk-api-1`
- `codewalk-postgres-1`
- `codewalk-caddy-1`

---

## 1. Quick health checklist

```bash
# ── External (no SSH) ──────────────────────────────────────────────
curl -s https://api.codewalk.xyz/health | python3 -m json.tool
# {"status": "ok"}

curl -s https://api.codewalk.xyz/version | python3 -m json.tool

# Webhook route mounted? 403 = good (missing signature). 404 = cloud not enabled.
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.codewalk.xyz/webhooks/github

# Admin list repos (POST not GET)
curl -s -X POST "$API/admin/repos" -H "X-Admin-Key: $ADMIN_API_KEY" | python3 -m json.tool

# ── On server ──────────────────────────────────────────────────────
cd /opt/codewalk
docker compose ps
docker compose ps -a

# All services healthy?
docker inspect --format='{{.Name}} {{.State.Health.Status}}' \
  codewalk-codewalk-api-1 2>/dev/null || docker compose ps
```

---

## 2. Docker & services

```bash
cd /opt/codewalk

# Status
docker compose ps
docker compose top codewalk-api

# Start / stop / restart
docker compose up -d
docker compose restart codewalk-api
docker compose restart postgres
docker compose restart caddy
docker compose up -d --force-recreate codewalk-api   # reload .env changes
docker compose down                                 # stop all (careful)

# Resource usage (indexing often shows 300–400% CPU on CPX21)
docker stats --no-stream
docker stats codewalk-codewalk-api-1

# Images
docker images ghcr.io/gupta29470/codewalk
docker inspect ghcr.io/gupta29470/codewalk:latest --format='{{.Id}} {{.Created}}'

# Deploy state
cat /opt/codewalk/.deploy-state    # line1=previous SHA, line2=current SHA
tail -50 /opt/codewalk/.deploy-log
```

---

## 3. Logs

```bash
cd /opt/codewalk

# Follow API logs (indexing, webhooks, worker)
docker compose logs -f codewalk-api

# Last N lines
docker compose logs --tail 200 codewalk-api
docker compose logs --tail 100 postgres
docker compose logs --tail 100 caddy

# Filter indexing / embedding progress
docker compose logs -f codewalk-api 2>&1 | grep -vE 'GET /health|404 Not Found'
docker compose logs -f codewalk-api | grep -iE 'embed|chunk|index|worker|webhook|error|failed'
docker compose logs codewalk-api 2>&1 | grep -iE 'embed|chunk|index|worker|webhook|error|failed'

# Worker thread
docker compose logs --tail 100 codewalk-api | grep -i worker

# Caddy / TLS
docker compose logs caddy | grep -iE 'error|certificate|tls'

# Since timestamp
docker compose logs --since 30m codewalk-api
docker compose logs --since 2h codewalk-api | grep -i embed
```

---

## 4. Cloud mode & environment

```bash
cd /opt/codewalk

# Cloud enabled? (needs GITHUB_APP_ID + PEM)
docker compose exec codewalk-api python -c "
from src.codewalk.api.cloud import is_cloud_enabled
print('cloud enabled:', is_cloud_enabled())
"

# GitHub App env inside container
docker compose exec codewalk-api python -c "
import os
from src.codewalk.worker.github_app import has_github_app_private_key
print('APP_ID:', os.environ.get('GITHUB_APP_ID'))
print('PEM path:', os.environ.get('GITHUB_APP_PRIVATE_KEY_PATH'))
print('PEM readable:', has_github_app_private_key())
print('EMBEDDING_MODEL:', os.environ.get('EMBEDDING_MODEL'))
print('DATABASE_URL set:', bool(os.environ.get('DATABASE_URL')))
"

# Write test (container runs as uid 999)
docker compose exec codewalk-api touch /var/codewalk/repos/.write_test
docker compose exec codewalk-api ls -la /var/codewalk/secrets/

# Compare .env on host (never paste secrets in chat)
grep -E '^[A-Z_]+=' /opt/codewalk/.env | cut -d= -f1 | sort
```

---

## 5. Admin API (curl)

> Header: **`X-Admin-Key`** (not `Authorization: Bearer`).  
> **`/admin/repos` and `/admin/index` use POST.**

```bash
export API="https://api.codewalk.xyz"
export ADMIN_API_KEY="..."

# List all repos + latest job per repo
curl -s -X POST "$API/admin/repos" \
  -H "X-Admin-Key: $ADMIN_API_KEY" | python3 -m json.tool

# Register repo manually (usually webhooks auto-register)
curl -s -X POST "$API/admin/register" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "owner/repo", "branch": "master"}' | python3 -m json.tool

# Trigger re-index (runs in API process — can take hours on CPU)
curl -s -X POST "$API/admin/index" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "gupta29470/codewalk"}' | python3 -m json.tool

# Optional branch override
curl -s -X POST "$API/admin/index" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "owner/repo", "branch": "release"}' | python3 -m json.tool

# Deployment version
curl -s "$API/version" | python3 -m json.tool
```

---

## 6. Index download, manifest & indexing lifecycle

### 6.1 How cloud indexing works

```
git push
  → webhook: queue job, supersede any in-flight run
  → build in indexes/{owner}/{repo}.incoming.{commit}/
  → write manifest → atomic_swap → indexes/{owner}/{repo}/ (active)

API restart / deploy
  → reconcile: fail orphaned queued/running jobs, indexing → pending
  → catch-up ~15s: full re-index for pending/failed/stuck/stale repos

Push while indexing
  → older job failed ("superseded by newer push")
  → only newest commit publishes (stale threads discard result)

Watchdog (every 10 min)
  → repos stuck in indexing > CODEWALK_STUCK_INDEX_MINUTES (default 30)
  → reset + catch-up retry
```

| Trigger | Index type | Publish |
|---------|------------|---------|
| GitHub `push` webhook | Incremental (seeds from active) | atomic swap |
| Post-deploy catch-up | Full rebuild | atomic swap |
| `POST /admin/index` | Incremental + supersede slot | atomic swap |

**Disk layout:**

| Path | Role |
|------|------|
| `indexes/{owner}/{repo}/` | Active index — download API + manifest |
| `indexes/{owner}/{repo}.incoming.{sha}/` | Per-run scratch (deleted after successful swap) |
| `indexes/{owner}/{repo}_old` | Brief backup during swap (auto-removed) |

**Status fields:**

| `repos.index_status` | Meaning |
|---------------------|---------|
| `pending` | Waiting for catch-up or manual trigger |
| `indexing` | Run in progress |
| `ready` | Active index matches `last_indexed_sha` |
| `failed` | Last run failed — catch-up or `reset-repo.sh` |

| `jobs.status` | Meaning |
|---------------|---------|
| `queued` → `running` → `done` | Normal webhook job |
| `failed` | Error or superseded / orphaned |

### 6.2 Download & manifest (MCP / laptop)

**MCP tools (preferred):** `codewalk_pull_index`, `codewalk_connect_repo`, or first `codewalk_analyze_codebase` when no local index — all **delete `{workspaceFolder}/.codewalk/` and extract fresh** (full replace, not merge). Index pull does not require MCP restart; **Codewalk code updates** (`git pull` on codewalk repo) require MCP restart in Cursor.

**Force re-download** when `pull_index` says “Already up to date” but index is wrong:

```bash
rm -rf /path/to/your/repo/.codewalk
# then codewalk_pull_index or codewalk_analyze_codebase in Cursor
```

**Manifest fields** (check `collection_name` matches repo slug, e.g. `codewalk` not stale `codebase`):

| Field | Meaning | When it changes |
|-------|---------|-----------------|
| `index_version` | Index generation counter (1, 2, 3…) | Every successful cloud re-index (+1) |
| `codewalk_version` | Codewalk **software** that wrote the index | When indexer container runs a newer `__version__` |
| `commit_sha` | Git commit of the **indexed repo** | Each webhook/admin re-index after `git pull` |

Log line format: `[manifest] Wrote ... (index vN, codewalk vX.Y.Z)` — first number is `index_version`, second is `codewalk_version`.

**Do not confuse** repo `git pull` (`/var/codewalk/repos/owner/repo`) with API deploy — only the **Docker container** version stamps `codewalk_version`.

```bash
# Get repo_token from DB first (see §7), then:
export REPO_TOKEN="cw_repo_xxxx"

curl -s "$API/indexes/$OWNER/$NAME/manifest" \
  -H "X-Repo-Token: $REPO_TOKEN" | python3 -m json.tool

# Manual tarball download (same bytes MCP pulls)
curl -s -o /tmp/index.tar.gz "$API/indexes/$OWNER/$NAME" \
  -H "X-Repo-Token: $REPO_TOKEN"
# Extract: rm -rf .codewalk && tar -xzf /tmp/index.tar.gz -C /path/to/repo
ls -lh /tmp/index.tar.gz
```

**After server re-index** (§11): bump `index_version` on cloud → laptop `codewalk_pull_index` or `rm -rf .codewalk` + pull.

**Stale-index notifications (MCP):** When cloud env is set, every MCP tool may prepend `[Cloud]` / `[Local]` banners. Cloud users: `codewalk_pull_index` for index updates; wait for server catch-up if manifest `codewalk_version` lags API `/version`. Local-only: `codewalk_analyze_codebase`. Software updates: `git pull` + **restart MCP**. `codewalk_index_status` compares local vs cloud.

**Local API / frontend:** `GET /staleness`, `GET /version`, and `X-Codewalk-Staleness` header on query endpoints (local mode only, not cloud-only).

---

## 7. PostgreSQL — indexing & webhooks

```bash
cd /opt/codewalk

# Open psql shell
docker compose exec postgres psql -U codewalk -d codewalk

# ── Repos overview ─────────────────────────────────────────────────
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT full_name, branch, index_status, last_indexed_sha, index_version,
          updated_at, created_at
   FROM repos ORDER BY updated_at DESC;"

# Single repo
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT * FROM repos WHERE full_name='${REPO}';"

# Repo token (for MCP X-Repo-Token)
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT full_name, repo_token, index_status, index_version FROM repos;"

# Count by status
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT index_status, COUNT(*) FROM repos GROUP BY index_status;"

# Stuck? indexing but updated_at frozen for hours
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT full_name, index_status, updated_at,
          NOW() - updated_at AS stale_for
   FROM repos WHERE index_status='indexing';"

# ── Jobs ───────────────────────────────────────────────────────────
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT repo_name, commit_sha, status, error,
          queued_at, started_at, finished_at
   FROM jobs ORDER BY queued_at DESC LIMIT 20;"

docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT * FROM jobs WHERE repo_name='${REPO}' ORDER BY queued_at DESC LIMIT 10;"

docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT status, COUNT(*) FROM jobs GROUP BY status;"

# Queued jobs waiting for worker
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT id, repo_name, commit_sha, queued_at FROM jobs
   WHERE status='queued' ORDER BY queued_at;"

# ── Webhook deliveries ─────────────────────────────────────────────
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT event_type, repo_full_name, commit_sha, status, error, delivery_id, created_at
   FROM webhook_deliveries ORDER BY created_at DESC LIMIT 20;"

docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT * FROM webhook_deliveries WHERE repo_full_name='${REPO}'
   ORDER BY created_at DESC LIMIT 10;"
```

**`index_status` values:** `pending` → `indexing` → `ready` (or `failed`)

**`jobs.status` values:** `queued` → `running` → `done` (or `failed` / superseded)

**Concurrent pushes:** Newer push fails older `queued`/`running` jobs and takes the index slot; only the latest commit is published.

**Stuck indexing:** Usually self-heals on deploy (reconcile + catch-up) or via watchdog (`CODEWALK_STUCK_INDEX_MINUTES`, default 30). Manual: §11 `prepare --index`.

---

## 8. Disk, indexes & clones (filesystem)

```bash
# Overall disk
df -h /
df -h /var/codewalk

# Per-repo index size (grows during embed — small chroma mid-run is normal)
du -sh /var/codewalk/indexes/*
du -sh /var/codewalk/indexes/${REPO}

# Index contents
ls -la /var/codewalk/indexes/${REPO}/
ls -la /var/codewalk/indexes/${REPO}/chroma/ 2>/dev/null
ls -la /var/codewalk/indexes/${REPO}/manifest.json 2>/dev/null

# Clone on disk
du -sh /var/codewalk/repos/${REPO}
ls -la /var/codewalk/repos/${REPO}/.git/HEAD 2>/dev/null
cat /var/codewalk/repos/${REPO}/codewalk.yaml 2>/dev/null

# Permissions (API container user = uid 999)
ls -la /var/codewalk/
ls -la /var/codewalk/repos/
ls -la /var/codewalk/secrets/

# HuggingFace cache (check both mounts — compose may use /root or /home/codewalk)
du -sh /root/.cache/huggingface 2>/dev/null
du -sh /var/codewalk/hf-cache 2>/dev/null
docker compose exec codewalk-api du -sh /root/.cache/huggingface 2>/dev/null
docker compose exec codewalk-api du -sh /home/codewalk/.cache/huggingface 2>/dev/null

# Docker disk
docker system df
```

---

## 9. System resources (host)

```bash
# CPU / RAM
free -h
top -bn1 | head -20
htop   # if installed

# Load during indexing (Jina 1.5B on CPU can peg 4 vCPUs for hours)
uptime
nproc

# Open ports
ss -tlnp | grep -E ':80|:443|:8000|:5432'

# Swap (add if OOM during embed)
swapon --show
```

---

## 10. Deploy & update code

**Post-deploy catch-up (automatic):** On API startup, orphaned `indexing`/`queued` jobs are cancelled and repos reset to `pending`. ~15s later, catch-up re-indexes repos that are pending/failed, stuck in `indexing` (>30 min, `CODEWALK_STUCK_INDEX_MINUTES`), behind their latest job commit, or whose manifest `codewalk_version` is older than the running container. A watchdog runs every 10 min for long-running stuck jobs. Expect one extra `index_version` bump per semver deploy. Verify:

```bash
curl -s "$API/version" | python3 -m json.tool          # running API version
docker compose logs codewalk-api | grep -E 'catchup|manifest' | tail -20
curl -s "$API/indexes/$OWNER/$NAME/manifest" \
  -H "X-Repo-Token: $REPO_TOKEN" | python3 -m json.tool  # codewalk_version should match /version
```

Laptop after catch-up: `codewalk_pull_index`. Bump `__init__.py` + `deploy/pyproject.toml` only for **named releases**; routine pushes notify via `commit_sha` without a semver bump.

```bash
# ── CI/CD path (normal) ────────────────────────────────────────────
# git push master → GitHub Actions → deploy-server.sh on server

# ── Manual deploy on server ────────────────────────────────────────
cd /opt/codewalk-src && git pull origin master
DEPLOY_SHA=$(git rev-parse --short HEAD)
DEPLOY_SHA=$DEPLOY_SHA /opt/codewalk-src/deploy/deploy-server.sh

# Or pull image only (no compose sync)
cd /opt/codewalk
docker compose pull codewalk-api
docker compose up -d --remove-orphans

# ── Code on disk ≠ running image — rebuild if GHCR unavailable ─────
cd /opt/codewalk-src
docker build -f deploy/Dockerfile -t ghcr.io/gupta29470/codewalk:latest .
cd /opt/codewalk
docker compose up -d --force-recreate codewalk-api

# Sync compose + Caddyfile from src without full deploy script
cp /opt/codewalk-src/deploy/docker-compose.yml /opt/codewalk/
cp /opt/codewalk-src/deploy/Caddyfile /opt/codewalk/
docker compose up -d

# Force redeploy same SHA
rm /opt/codewalk/.deploy-state
DEPLOY_SHA=abc1234 /opt/codewalk-src/deploy/deploy-server.sh

# Rollback image
docker images ghcr.io/gupta29470/codewalk
docker tag ghcr.io/gupta29470/codewalk:sha-OLD ghcr.io/gupta29470/codewalk:latest
cd /opt/codewalk && docker compose up -d --force-recreate codewalk-api
```

---

## 11. Re-index, reset & fix stuck indexing

> **Preferred:** use `deploy/reset-repo.sh` — works for **new** and **existing** repos. Detects on-disk index: **update** (incremental) vs **create** (full embed). Every mode supports **`--dry-run`**.

```bash
cd /opt/codewalk-src
chmod +x deploy/reset-repo.sh deploy/ensure-storage.sh

# Inspect state + plans (no changes)
./deploy/reset-repo.sh inspect gupta29470/codewalk

# Dry-run any action first
./deploy/reset-repo.sh prepare gupta29470/codewalk --index --dry-run
./deploy/reset-repo.sh soft-reset gupta29470/codewalk --index --dry-run

# Smart prepare: existing index → UPDATE (incremental); new → CREATE + register
./deploy/reset-repo.sh prepare gupta29470/codewalk --index

# Fix permissions — one repo or all
./deploy/reset-repo.sh fix-perms
./deploy/reset-repo.sh fix-perms gupta29470/codewalk --dry-run

# Force wipe index (keep clone) + full re-create
./deploy/reset-repo.sh soft-reset gupta29470/codewalk --index

# Force wipe index + clone
./deploy/reset-repo.sh full-reset gupta29470/codewalk --index

# Delete from DB + disk
./deploy/reset-repo.sh delete-repo gupta29470/codewalk --dry-run
```

**Coverage matrix** (see `inspect` for your repo):

| Situation | Command |
|-----------|---------|
| New repo (no DB, no index) | `prepare owner/repo --index` → register + CREATE |
| Existing index OK | `prepare owner/repo --index` → UPDATE (incremental) |
| Permissions broken (code 14) | `fix-perms owner/repo` then `prepare --index` |
| `index_status=failed` / stuck job | `prepare owner/repo --index` (unsticks + retries) |
| Corrupt index (chroma, no manifest) | `soft-reset owner/repo --index` |
| Force full rebuild | `soft-reset` (keep clone) or `full-reset` (wipe clone) |
| Remove repo entirely | `delete-repo` → then `prepare --index` or git push |
| All repos perms | `fix-perms` (no slug) |
| Preview any of the above | add `--dry-run` or `inspect owner/repo` |

**Not in script** (manual): private-repo clone auth (GitHub App PEM), DuckDB `.wal` lock delete, webhook branch allowlist (`codewalk.yaml`), laptop `rm -rf .codewalk` + MCP pull.

`deploy-server.sh` and `hetzner-setup.sh` call `ensure-storage.sh` so deploys keep `/var/codewalk` writable.

**Manual equivalents** (if script unavailable):

```bash
cd /opt/codewalk
export REPO="gupta29470/codewalk"

# ── Soft re-index (keep clone, wipe index artifacts) ─────────────────
rm -rf /var/codewalk/indexes/${REPO}
/opt/codewalk-src/deploy/ensure-storage.sh "${REPO}"

docker compose exec postgres psql -U codewalk -d codewalk -c \
  "UPDATE repos SET last_indexed_sha=NULL, index_status='pending', index_version=0
   WHERE full_name='${REPO}';"

curl -s -X POST https://api.codewalk.xyz/admin/index \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"full_name\": \"${REPO}\"}" | python3 -m json.tool

# ── Full reset (index + clone) ─────────────────────────────────────
rm -rf /var/codewalk/indexes/${REPO} /var/codewalk/repos/${REPO}
/opt/codewalk-src/deploy/ensure-storage.sh "${REPO}"
# then UPDATE repos + admin/index as above

# ── Unstick zombie row (indexing for hours, job still queued) ───────
# 1. Check logs still show "Embedded N/M" → not dead, just slow on CPU
docker compose logs --tail 30 codewalk-api | grep -i embed

# 2. If truly stuck — reset DB and restart API
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "UPDATE repos SET index_status='pending' WHERE full_name='${REPO}';"
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "UPDATE jobs SET status='failed', error='cancelled by admin', finished_at=NOW()
   WHERE repo_name='${REPO}' AND status IN ('queued','running');"
docker compose restart codewalk-api

# ── Failed job — inspect error ─────────────────────────────────────
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT error, commit_sha, finished_at FROM jobs
   WHERE repo_name='${REPO}' AND status='failed'
   ORDER BY finished_at DESC LIMIT 3;"
```

---

## 12. Permissions & common fixes

```bash
# Permission denied on /var/codewalk/repos (Chroma code 14: unable to open database file)
/opt/codewalk-src/deploy/ensure-storage.sh
# or per-repo:
/opt/codewalk-src/deploy/ensure-storage.sh gupta29470/codewalk

# Manual equivalent:
chown -R 999:999 /var/codewalk
chmod 600 /var/codewalk/secrets/*.pem

# Postgres password mismatch after .env change
docker compose exec postgres psql -U codewalk -d postgres -c \
  "ALTER USER codewalk WITH PASSWORD 'password-from-env-without-special-chars';"
docker compose up -d --force-recreate codewalk-api

# DuckDB lock after crash
find /var/codewalk -name '*.wal' -ls
# rm specific .wal only if API won't start

# Caddy port conflict
ss -tlnp | grep -E ':80|:443'
systemctl stop apache2 nginx 2>/dev/null

# OOM / swap
fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
```

---

## 13. GitHub webhook debugging

| Symptom | Check |
|---------|-------|
| `repos: []` after push | GitHub App **installed** on repo? Push in App → Recent Deliveries? |
| Webhook 404 | Cloud mode off — fix PEM / `GITHUB_APP_ID`, `--force-recreate codewalk-api` |
| Webhook 403 on curl | Expected without `X-Hub-Signature-256` |
| Push ignored | Branch not in `codewalk.yaml` `indexing.branches` |
| Index never starts | `SELECT * FROM jobs WHERE status='queued'` + worker logs |

```bash
# DB: recent webhook events
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT event_type, repo_full_name, status, error, created_at
   FROM webhook_deliveries ORDER BY created_at DESC LIMIT 10;"
```

**GitHub UI:** App settings → **Recent deliveries** → compare `delivery_id` with DB.

---

## 14. Copy-paste monitoring loop

```bash
# Run on server while indexing — refreshes every 30s
watch -n 30 '
  echo "=== REPOS ==="
  docker compose -f /opt/codewalk/docker-compose.yml exec -T postgres \
    psql -U codewalk -d codewalk -c \
    "SELECT full_name, index_status, updated_at FROM repos ORDER BY updated_at DESC;"
  echo "=== JOBS ==="
  docker compose -f /opt/codewalk/docker-compose.yml exec -T postgres \
    psql -U codewalk -d codewalk -c \
    "SELECT repo_name, status, queued_at FROM jobs ORDER BY queued_at DESC LIMIT 5;"
  echo "=== EMBED LOG ==="
  docker compose -f /opt/codewalk/docker-compose.yml logs --tail 3 codewalk-api 2>&1 | grep -i embed || true
  echo "=== DOCKER STATS ==="
  docker stats --no-stream codewalk-codewalk-api-1 2>/dev/null | tail -1
'
```

---

## 15. Quick reference table

| What | Command |
|------|---------|
| API health | `curl -s https://api.codewalk.xyz/health` |
| Version | `curl -s https://api.codewalk.xyz/version` |
| Cloud on | `docker compose exec codewalk-api python -c "from src.codewalk.api.cloud import is_cloud_enabled; print(is_cloud_enabled())"` |
| Services up | `cd /opt/codewalk && docker compose ps` |
| Live logs | `docker compose logs -f codewalk-api` |
| Embed progress | `docker compose logs codewalk-api 2>&1 \| grep -i embed` |
| List repos | `curl -s -X POST $API/admin/repos -H "X-Admin-Key: $ADMIN_API_KEY"` |
| Repo status SQL | `SELECT full_name, index_status, updated_at FROM repos;` |
| Jobs SQL | `SELECT repo_name, status, error FROM jobs ORDER BY queued_at DESC LIMIT 10;` |
| Webhooks SQL | `SELECT * FROM webhook_deliveries ORDER BY created_at DESC LIMIT 10;` |
| Repo token | `SELECT repo_token FROM repos WHERE full_name='owner/repo';` |
| Index size | `du -sh /var/codewalk/indexes/owner/repo` |
| Trigger index | `curl -s -X POST $API/admin/index -H "X-Admin-Key: ..." -d '{"full_name":"owner/repo"}'` |
| Manifest | `curl -s $API/indexes/owner/repo/manifest -H "X-Repo-Token: ..."` |
| Deploy SHA | `cat /opt/codewalk/.deploy-state` |
| CPU/RAM | `docker stats --no-stream` |

---

## Related docs

- [FULL_SETUP_GUIDE.md](../FULL_SETUP_GUIDE.md) — first-time setup, GitHub App, MCP
- [DEPLOY.md](./DEPLOY.md) — provisioning, CI/CD, rollback
- [Cloud Ops Dashboard](../codewalk_plan.md#cloud-ops-dashboard-todo--connect-ui-to-production-server) — planned UI for these checks

*Last updated: 2026-06-11*
