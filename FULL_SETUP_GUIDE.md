# Codewalk — Full Setup Guide (Step by Step)

> **Production example:** `api.codewalk.xyz` (API + indexing) · `codewalk.xyz` (optional marketing site)  
> **Time:** ~60 min first time · ~5 min per deploy after that

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Prerequisites](#2-prerequisites)
3. [Phase 1 — GitHub repo & CI/CD](#3-phase-1--github-repo--cicd)
4. [Phase 2 — Hetzner server & DNS](#4-phase-2--hetzner-server--dns)
5. [Phase 3 — Server `.env` & secrets](#5-phase-3--server-env--secrets)
6. [Phase 4 — First deploy & cloud mode](#6-phase-4--first-deploy--cloud-mode)
7. [Phase 5 — GitHub App (auto-indexing)](#7-phase-5--github-app-auto-indexing)
8. [Phase 6 — First push & verify index](#8-phase-6--first-push--verify-index)
9. [Phase 7 — Team config (`codewalk.yaml`)](#9-phase-7--team-config-codewalkyaml)
10. [Phase 8 — Local MCP (download index)](#10-phase-8--local-mcp-download-index)
11. [Phase 9 — Ongoing deploys](#11-phase-9--ongoing-deploys)
12. [Admin commands](#12-admin-commands)
13. [Delete / re-index](#13-delete--re-index)
14. [Troubleshooting](#14-troubleshooting)
15. [Optional: marketing site (`codewalk.xyz`)](#15-optional-marketing-site-codewalkxyz)

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ GITHUB                                                               │
│  git push ──► GitHub App webhook ──► api.codewalk.xyz/webhooks/github│
│              (auto-index)                                            │
│                                                                      │
│  git push master ──► GitHub Actions ──► build image ──► deploy VPS  │
│              (updates server code only — NOT indexing trigger alone) │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ HETZNER (api.codewalk.xyz)                                           │
│  Caddy :443 ──► codewalk-api :8000 ──► Postgres                     │
│  /var/codewalk/indexes/{owner}/{repo}/  ← index artifacts            │
│  /var/codewalk/repos/{owner}/{repo}/    ← cloned source              │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                          GET /indexes/{owner}/{repo}
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│ YOUR LAPTOP                                                          │
│  Clone codewalk repo → run MCP server                                │
│  Open target repo in Cursor → MCP downloads index → query locally    │
└─────────────────────────────────────────────────────────────────────┘
```

| Component | Role |
|-----------|------|
| **Cloud server** | Indexing only (webhooks, embeddings, storage) |
| **GitHub Actions** | Build Docker image + deploy server |
| **GitHub App** | Send `push` webhooks → trigger indexing |
| **Local MCP** | Download index, query code locally |

### What triggers repo registration & indexing?

| Step | Registers repo in DB? | Starts indexing? |
|------|----------------------|------------------|
| GitHub Actions secrets + green deploy | No | No (updates server code only) |
| Create GitHub App | No | No |
| **Install** App on a repo | No | No (`installation` webhook is ignored) |
| Webhook **ping** test | No | No (returns `pong` only) |
| **`git push`** to installed repo | **Yes** (creates `repos` row + `repo_token`) | **Yes** |
| `POST /admin/register` (manual) | Yes | No — still need push or `POST /admin/index` |

**Minimal flow to index a repo** (server already running):

1. Install GitHub App on that repo
2. Push a commit (empty commit is fine)
3. Wait for `index_status: ready` → copy `repo_token` for MCP

GitHub Actions secrets (`HETZNER_*`) are **only** for auto-deploying the codewalk server when you push to `master`. They are **not** required for indexing.

**Config file index:**

| File | Where |
|------|-------|
| `env.server.example.txt` | Template → `/opt/codewalk/.env` on server |
| `env.local.example.txt` | Template → local `codewalk/.env` |
| `env.example.txt` | Full reference for all env vars |
| `codewalk.yaml` | Per-repo config (`indexing.branches`, excludes) — in **each indexed repo** |
| `.vscode/mcp.json` | Per-project MCP config |

---

## 2. Prerequisites

| Item | Notes |
|------|-------|
| GitHub account | Repo, Actions, GHCR, GitHub App |
| Domain | `api.codewalk.xyz` A record → server IP |
| Hetzner VPS | CPX21 recommended (4 vCPU, 8 GB RAM) |
| SSH key | `ssh-keygen -t ed25519 -f ~/.ssh/hetzner_codewalk` |

---

## 3. Phase 1 — GitHub repo & CI/CD

### Step 1.1 — Push code

```bash
git remote add origin https://github.com/YOUR_USERNAME/codewalk.git
git branch -M master
git push -u origin master
```

> Default branch must be **`master`** (deploy workflow uses `master`).

### Step 1.2 — GHCR package public (if repo is public)

`https://github.com/YOUR_USERNAME/codewalk/pkgs/container/codewalk` → **Package settings** → **Public**

### Step 1.3 — GitHub Actions secrets

**Repo → Settings → Secrets and variables → Actions:**

| Secret | Value |
|--------|-------|
| `HETZNER_HOST` | Server IP |
| `HETZNER_USER` | `root` |
| `HETZNER_SSH_KEY` | Private SSH key contents |

### Step 1.4 — Verify Actions

Push to `master` → **Actions** tab → **Build & Deploy** should go green.

> Actions deploys the **server image**. It does **not** register repos or start indexing.

---

## 4. Phase 2 — Hetzner server & DNS

### Step 2.1 — Create VPS

Ubuntu 24.04 · CPX21 · firewall: TCP 22 (your IP), 80, 443.

### Step 2.2 — DNS

| Type | Host | Value |
|------|------|-------|
| A | `api` | `YOUR_SERVER_IP` |
| A | `@` | `YOUR_SERVER_IP` (optional, for `codewalk.xyz`) |

```bash
dig +short api.codewalk.xyz   # should return server IP
```

### Step 2.3 — Server directories

```bash
ssh -i ~/.ssh/hetzner_codewalk root@YOUR_SERVER_IP

mkdir -p /opt/codewalk /opt/codewalk-src
mkdir -p /var/codewalk/repos /var/codewalk/indexes /var/codewalk/secrets
chown -R 999:999 /var/codewalk    # API container runs as uid 999
chmod 755 /var/codewalk
```

Or run `deploy/hetzner-setup.sh` (also creates `/opt/codewalk` layout).

### Step 2.4 — Clone source on server

```bash
git clone https://github.com/YOUR_USERNAME/codewalk.git /opt/codewalk-src
cp /opt/codewalk-src/deploy/docker-compose.yml /opt/codewalk/
cp /opt/codewalk-src/deploy/Caddyfile /opt/codewalk/
cp /opt/codewalk-src/deploy/deploy-server.sh /opt/codewalk/
chmod +x /opt/codewalk/deploy-server.sh
```

**Runtime layout:**

| Path | Purpose |
|------|---------|
| `/opt/codewalk` | `docker-compose.yml`, `.env`, `Caddyfile` — run compose here |
| `/opt/codewalk-src` | Git clone (CI updates via `git pull`) |
| `/var/codewalk` | Indexes, cloned repos, PEM secrets |

---

## 5. Phase 3 — Server `.env` & secrets

### Step 3.1 — Copy template

```bash
cp /opt/codewalk-src/env.server.example.txt /opt/codewalk/.env
nano /opt/codewalk/.env
```

### Step 3.2 — Fill required values

```env
EMBEDDING_MODEL=jinaai/jina-code-embeddings-1.5b

# No @ : / # in password!
POSTGRES_PASSWORD=your-strong-password-here

GITHUB_APP_ID=4025425
GITHUB_APP_PRIVATE_KEY_PATH=/var/codewalk/secrets/codewalk-cloud.private-key.pem
GITHUB_WEBHOOK_SECRET=your-64-char-hex-secret
ADMIN_API_KEY=your-64-char-hex-admin-key
```

Generate secrets:

```bash
openssl rand -hex 32   # webhook secret + admin key
openssl rand -base64 24   # postgres password (avoid @)
```

### Step 3.3 — Install GitHub App private key

```bash
# On server — copy PEM from your machine:
# scp -i ~/.ssh/hetzner_codewalk ./codewalk-cloud.pem root@IP:/var/codewalk/secrets/

chmod 600 /var/codewalk/secrets/*.pem
chown 999:999 /var/codewalk/secrets/*.pem
```

> **Container path** must be `/var/codewalk/secrets/...` — NOT `/opt/codewalk-src/...`

### Step 3.4 — Caddyfile

`/opt/codewalk/Caddyfile`:

```caddy
api.codewalk.xyz {
    reverse_proxy codewalk-api:8000
}
```

---

## 6. Phase 4 — First deploy & cloud mode

### Step 4.1 — Start services

```bash
cd /opt/codewalk
docker compose up -d
```

First time (before GHCR image exists):

```bash
cd /opt/codewalk-src
docker build -f deploy/Dockerfile -t ghcr.io/YOUR_USERNAME/codewalk:latest .
cd /opt/codewalk && docker compose up -d
```

### Step 4.2 — Verify health

```bash
curl https://api.codewalk.xyz/health
# {"status":"ok"}

docker compose ps
# postgres, codewalk-api, caddy all Up
```

### Step 4.3 — Verify cloud mode

```bash
docker compose exec codewalk-api python -c "
from src.codewalk.api.cloud import is_cloud_enabled
print('cloud enabled:', is_cloud_enabled())
"
# cloud enabled: True

docker compose exec codewalk-api touch /var/codewalk/repos/.write_test
# must succeed — if Permission denied: chown -R 999:999 /var/codewalk
```

### Step 4.4 — Verify webhook route

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.codewalk.xyz/webhooks/github
# 403 = route exists (signature missing — expected)
# 404 = cloud routes not mounted — fix PEM / GITHUB_APP_ID / recreate container
```

### Step 4.5 — If you changed `POSTGRES_PASSWORD` after first start

```bash
docker compose exec postgres psql -U codewalk -d postgres \
  -c "ALTER USER codewalk WITH PASSWORD 'your-password-from-env';"
docker compose up -d --force-recreate codewalk-api
```

---

## 7. Phase 5 — GitHub App (auto-indexing)

> **Creating the app ≠ installing it.** Push webhooks only fire for **installed** repos.

### Step 5.1 — Create the app

1. `https://github.com/settings/apps/new`
2. Fill in:

| Field | Value |
|-------|-------|
| **GitHub App name** | `Codewalk Cloud` (globally unique) |
| **Homepage URL** | `https://codewalk.xyz` or server IP |
| **Webhook URL** | `https://api.codewalk.xyz/webhooks/github` |
| **Webhook secret** | Same as `GITHUB_WEBHOOK_SECRET` in `.env` |

3. **Permissions:**

| Permission | Access |
|------------|--------|
| Repository contents | Read-only |
| Metadata | Read-only |
| Pull requests | Read-only (optional) |

4. **Subscribe to events:** ✅ **Push** (required)

5. Click **Create GitHub App**

### Step 5.2 — Generate private key

**App settings → Private keys → Generate** → download `.pem` → copy to `/var/codewalk/secrets/` (Step 3.3).

Note **App ID** from the top of the app settings page → `GITHUB_APP_ID` in `.env`.

### Step 5.3 — Install the app ⚠️ CRITICAL

> Installing registers **nothing** in Postgres yet. The repo row (and `repo_token`) is created on the **first `push` webhook** (Phase 6).

1. App page → **Install App** (left sidebar)
2. Select your account
3. **Only select repositories** → pick `codewalk` (or **All repositories**)
4. Click **Install**

Verify: **Repo → Settings → Integrations → GitHub Apps** shows **Codewalk Cloud**.

### Step 5.4 — Test webhook (ping)

1. **App → Advanced → Recent Deliveries**
2. Open latest `ping` → **Redeliver** if it failed earlier (404 before cloud was on)
3. Expected response: `{"status":"pong"}` · HTTP **200**

On server:

```bash
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT event_type, status FROM webhook_deliveries ORDER BY created_at DESC LIMIT 3;"
# ping | received
```

### Step 5.5 — Recreate API if you changed `.env`

```bash
cd /opt/codewalk
docker compose up -d --force-recreate codewalk-api
```

---

## 8. Phase 6 — First push & verify index

### Step 8.1 — Push (triggers indexing)

```bash
git commit --allow-empty -m "test: trigger codewalk index"
git push origin master
```

> Old pushes are **not** replayed after install. You need a **new** push.

### Step 8.2 — Check webhook delivery

**App → Recent Deliveries** → `push` event → green **200**

On server:

```bash
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT event_type, repo_full_name, status FROM webhook_deliveries ORDER BY created_at DESC LIMIT 5;"
```

### Step 8.3 — Check repo status

```bash
curl -s -X POST https://api.codewalk.xyz/admin/repos \
  -H "X-Admin-Key: YOUR_ADMIN_KEY" | python3 -m json.tool
```

Expected progression: `index_status: "indexing"` → `"ready"`.

First index takes **5–15+ minutes** (model download + full scan).

### Step 8.4 — Watch logs

```bash
docker compose logs -f codewalk-api 2>&1 | grep -vE 'GET /health|404 Not Found'
```

### Step 8.5 — Get download token for MCP

```bash
docker compose exec postgres psql -U codewalk -d codewalk -c \
  "SELECT full_name, repo_token FROM repos;"
```

---

## 9. Phase 7 — Team config (`codewalk.yaml`)

Each **indexed repo** can have a `codewalk.yaml` at its root. Cloud reads it on every index.

```yaml
# codewalk.yaml — repo root
indexing:
  # Only these branches trigger cloud indexing (fnmatch; pushes to others are ignored)
  branches:
    - master
    - release
    - release/**
    - release-*
  exclude:
    - frontend/**
    - assets/**
    - docs/**
    - tests/**
    - env.example.txt

guidelines_path: docs/standards   # optional
docs_path: docs                   # optional
```

| Pattern | Matches |
|---------|---------|
| `frontend/**` | Whole directory tree |
| `README.md` | Exact filename |
| `**/*.g.dart` | Glob |
| `release/**` | Branch `release/v2.0`, etc. |

Built-in `file_filter.py` already skips `node_modules`, `.next`, `__pycache__`, `.git`, lock files, binaries.

**Branch filter:** Pushes to `feature/foo` are ignored if not in `indexing.branches`. One index per repo — last allowed-branch push wins. Push an allowed branch first so `codewalk.yaml` exists on the server clone (until then, only `master` is allowed by default).

Commit and push → cloud re-indexes with new excludes / branch rules.

---

## 10. Phase 8 — Local MCP (download index)

### Step 10.1 — Clone codewalk locally (MCP server)

```bash
git clone https://github.com/YOUR_USERNAME/codewalk.git
cd codewalk
python -m venv .codewalk-env && source .codewalk-env/bin/activate
pip install -r requirements.txt
```

### Step 10.2 — Open target repo in Cursor

The **workspace** is the repo you want to query (e.g. `codewalk` itself).

### Step 10.3 — MCP config (`.cursor/mcp.json` or `.vscode/mcp.json`)

Copy template: `cp mcp.json.example .vscode/mcp.json` (in target project or codewalk repo), then edit paths and `CODEWALK_REPO_TOKEN`.

```json
{
  "servers": {
    "codewalk": {
      "command": "/path/to/codewalk/.codewalk-env/bin/python",
      "args": ["-m", "src.codewalk.mcp.server"],
      "cwd": "/path/to/codewalk",
      "env": {
        "REPO_PATH": "${workspaceFolder}",
        "CODEWALK_SERVER_URL": "https://api.codewalk.xyz",
        "CODEWALK_REPO_NAME": "gupta29470/codewalk",
        "CODEWALK_REPO_TOKEN": "cw_repo_xxxxxxxx"
      }
    }
  }
}
```

### Step 10.4 — Connect & download

In Cursor, run MCP tool **`codewalk_connect_repo`** or start analyzing — MCP calls:

```
GET https://api.codewalk.xyz/indexes/{owner}/{repo}/manifest
GET https://api.codewalk.xyz/indexes/{owner}/{repo}
```

Index extracts to `.codewalk/` in the git root.

### Step 10.5 — Test manifest from laptop

```bash
curl -s https://api.codewalk.xyz/indexes/gupta29470/codewalk/manifest \
  -H "X-Repo-Token: cw_repo_xxxx" | python3 -m json.tool
```

---

## 11. Phase 9 — Ongoing deploys

```
git push master
  → GitHub Actions builds image → pushes GHCR
  → SSH deploy: git pull /opt/codewalk-src
  → deploy-server.sh: pull image, sync compose+Caddyfile, restart
```

Manual on server:

```bash
cd /opt/codewalk-src && git pull origin master
DEPLOY_SHA=$(git rev-parse --short HEAD) /opt/codewalk/deploy-server.sh
```

**Auto-synced on deploy:** `docker-compose.yml`, `Caddyfile`, `deploy-server.sh`  
**Manual only:** `/opt/codewalk/.env`, PEM files in `/var/codewalk/secrets/`

**Image retention:** `latest` + current SHA + previous SHA (~15–20 GB normal).

---

## 12. Admin commands

All admin routes use header **`X-Admin-Key`** (not `Authorization: Bearer`).

```bash
export ADMIN_API_KEY="your-key"
export API="https://api.codewalk.xyz"

# List repos
curl -s -X POST "$API/admin/repos" -H "X-Admin-Key: $ADMIN_API_KEY" | python3 -m json.tool

# Trigger re-index
curl -s -X POST "$API/admin/index" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "gupta29470/codewalk"}' | python3 -m json.tool

# Manual register (optional — webhooks auto-register)
curl -s -X POST "$API/admin/register" \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"full_name": "owner/repo", "branch": "master"}'
```

---

## 13. Delete / re-index

### Index files only

```bash
REPO="gupta29470/codewalk"
rm -rf /var/codewalk/indexes/${REPO}

docker compose exec postgres psql -U codewalk -d codewalk -c \
  "UPDATE repos SET last_indexed_sha=NULL, index_status='pending', index_version=0 WHERE full_name='${REPO}';"

curl -s -X POST https://api.codewalk.xyz/admin/index \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"full_name\": \"${REPO}\"}"
```

### Full reset (index + clone)

```bash
rm -rf /var/codewalk/indexes/${REPO} /var/codewalk/repos/${REPO}
# then UPDATE repos + admin/index as above
```

### Local MCP index

```bash
rm -rf .codewalk   # in target repo root
```

---

## 14. Troubleshooting

### `cloud enabled: False`

```bash
docker compose exec codewalk-api python -c "
import os
from src.codewalk.worker.github_app import has_github_app_private_key
print('APP_ID:', bool(os.environ.get('GITHUB_APP_ID')))
print('PEM:', has_github_app_private_key())
print('PATH:', os.environ.get('GITHUB_APP_PRIVATE_KEY_PATH'))
"
```

| Issue | Fix |
|-------|-----|
| PEM `False` | PEM must be at `/var/codewalk/secrets/...` (container path) |
| Missing APP_ID | Add to `.env`, `--force-recreate codewalk-api` |

### Webhook ping → 404 `Not Found`

Cloud routes not mounted when ping was sent. Fix cloud mode → **Redeliver** ping in GitHub.

### `repos: []` after push

| Cause | Fix |
|-------|-----|
| App not **installed** | Install App → select repo |
| No `push` in Recent Deliveries | Install + push again |
| Actions green but no index | Actions ≠ webhooks — need App install + push |

### `Permission denied: /var/codewalk/repos`

```bash
chown -R 999:999 /var/codewalk
# Do NOT use $UID in bash as root — it's readonly (stays 0)
```

### `could not translate host name "xxx@postgres"`

Password contains `@`. Use password without `@ : / #` or URL-encode. Run `ALTER USER` if changed.

### `password authentication failed`

`.env` password ≠ Postgres user password. Run `ALTER USER codewalk WITH PASSWORD '...'`.

### `405` on `/admin/repos`

Use **POST**, not GET: `curl -X POST ...`

### Indexing failed — clone auth

Private repos need GitHub App installation token (ensure app installed + contents read permission).

### Disk usage ~50 GB

Normal: ML dependencies in Docker image (~7 GB each) + HF cache + indexes. Keep 3 image tags by design.

---

## 15. Optional: marketing site (`codewalk.xyz`)

Add to `/opt/codewalk/Caddyfile`:

```caddy
api.codewalk.xyz {
    reverse_proxy codewalk-api:8000
}

codewalk.xyz, www.codewalk.xyz {
    root * /var/www/codewalk
    file_server
}
```

Add to `docker-compose.yml` under `caddy` volumes:

```yaml
- /var/www/codewalk:/var/www/codewalk:ro
```

```bash
mkdir -p /var/www/codewalk
# upload index.html, assets
docker compose up -d
```

Not required for indexing or MCP.

---

## Quick reference

| Check | Command |
|-------|---------|
| Health | `curl https://api.codewalk.xyz/health` |
| Cloud on | `is_cloud_enabled()` in container |
| Webhook route | `curl -X POST .../webhooks/github` → 403 |
| Repos | `POST /admin/repos` + `X-Admin-Key` |
| Webhook log | `SELECT * FROM webhook_deliveries` |
| Repo token | `SELECT repo_token FROM repos` |
| Deploy state | `cat /opt/codewalk/.deploy-state` |

**Domains:**

| URL | Purpose |
|-----|---------|
| `https://api.codewalk.xyz` | API, webhooks, index download |
| `https://codewalk.xyz` | Optional marketing site |
| `https://api.codewalk.xyz/indexes/owner/repo` | MCP index download |
