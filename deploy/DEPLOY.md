# Codewalk Deployment Guide — Hetzner Cloud

> One-stop guide for deploying Codewalk to a Hetzner VPS with Docker Compose, Caddy reverse proxy, and GitHub Actions CI/CD.

---

## 📋 Table of Contents

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Infrastructure Setup](#infrastructure-setup)
3. [Server Provisioning](#server-provisioning)
4. [Application Deployment](#application-deployment)
5. [Post-Deployment Verification](#post-deployment-verification)
6. [Future Deploys (CI/CD)](#future-deploys-cicd)
7. [Rollback Procedure](#rollback-procedure)
8. [Troubleshooting](#troubleshooting)

---

## Pre-Deployment Checklist

Before touching any server, verify these items locally.

### Code & Build

- [ ] All tests pass (if you have them)
- [ ] `python -m py_compile src/codewalk/**/*.py` — no syntax errors
- [ ] `deploy/Dockerfile` builds successfully locally:
  ```bash
  docker build -f deploy/Dockerfile -t codewalk:local .
  ```
- [ ] `.dockerignore` excludes unnecessary files (`frontend/`, `data/`, `.git/`)
- [ ] `deploy/docker-compose.yml` references correct image tag (`ghcr.io/...`)
- [ ] `deploy/Caddyfile` uses the correct domain name
- [ ] `src/codewalk/__init__.py` version matches `deploy/pyproject.toml`

### Secrets & Environment

- [ ] `.env` file created with all required variables (see [Environment Variables](#environment-variables))
- [ ] `POSTGRES_PASSWORD` is strong (≥20 chars, mixed case + numbers + symbols)
- [ ] LLM API key is valid and has sufficient quota
- [ ] `GITHUB_APP_PRIVATE_KEY` is the full PEM content (not a file path) if using cloud mode
- [ ] `ADMIN_API_KEY` is strong and unique

### DNS & Domain

- [ ] Domain registered and DNS A record points to Hetzner IP (or ready to point)
- [ ] Domain propagation checked: `dig +short codewalk.yourdomain.com`

### GitHub

- [ ] Repository has `master` branch as default
- [ ] GitHub Secrets configured (see [GitHub Secrets](#github-secrets))
- [ ] GitHub Container Registry (GHCR) enabled for the repo
- [ ] GitHub App created (see [GitHub App Setup](#github-app-setup)) — required for cloud mode

---

## Infrastructure Setup

### 1. Create Hetzner VPS

| Spec | Recommendation | Minimum |
|------|----------------|---------|
| **Type** | CPX21 (4 vCPU, 8GB RAM, 80GB NVMe) | CX21 (2 vCPU, 4GB RAM, 40GB) |
| **OS** | Ubuntu 24.04 LTS | Ubuntu 22.04 LTS |
| **Location** | Closest to your users | Any |
| **Name** | `codewalk-prod` | — |

**Why CPX21?** Embedding models load ~1-2GB into RAM. With 4GB total (CX21), you risk OOM during indexing. CPX21 gives headroom.

**Optional:** Add a Volume (≥50GB) mounted at `/var/codewalk` if you plan to index many large repos.

### 2. DNS Configuration

```
A     codewalk.yourdomain.com    →  <HETZNER_SERVER_IP>
```

Verify before proceeding:
```bash
dig +short codewalk.yourdomain.com
# Should return your Hetzner IP
```

### 3. Firewall (Hetzner Cloud Console)

Create a firewall and attach it to your server:

| Direction | Protocol | Port | Source | Description |
|-----------|----------|------|--------|-------------|
| Inbound | TCP | 22 | Your IP only | SSH |
| Inbound | TCP | 80 | Anywhere | HTTP → Caddy redirects to HTTPS |
| Inbound | TCP | 443 | Anywhere | HTTPS → Caddy reverse proxy |

**Do NOT expose port 8000.** The API is internal-only; Caddy proxies to it.

---

## Server Provisioning

### Step 1: SSH to Server

```bash
ssh root@<HETZNER_IP>
```

### Step 2: Run Setup Script

Copy the setup script to the server and run it:

```bash
# Option A: Copy from local machine
scp deploy/hetzner-setup.sh root@<HETZNER_IP>:/tmp/
ssh root@<HETZNER_IP> "bash /tmp/hetzner-setup.sh codewalk.yourdomain.com admin@yourdomain.com"

# Option B: Paste directly (if repo is public)
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/codewalk/master/deploy/hetzner-setup.sh | \
  bash -s "codewalk.yourdomain.com" "admin@yourdomain.com"
```

This script:
- Updates Ubuntu
- Installs Docker + Docker Compose
- Creates `/opt/codewalk/` directory
- Generates `docker-compose.yml`, `Caddyfile`, and `.env` template

### Step 3: Configure Environment Variables

```bash
nano /opt/codewalk/.env
```

Fill in **all** required values. See the full reference below.

#### Required Variables

```env
# ─── Database ────────────────────────────────────────────────────────
POSTGRES_PASSWORD=your-very-strong-password-here-20-chars-min

# ─── LLM Provider (pick ONE) ─────────────────────────────────────────
LLM_PROVIDER=groq
LLM_MODEL=llama-3.1-70b-versatile

# Fill in the key for your chosen provider:
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# GOOGLE_API_KEY=AI...
# OPENROUTER_API_KEY=sk-or-...
# DEEPSEEK_API_KEY=sk-...
```

#### Optional Variables

```env
# CORS — restrict to your frontend domain(s) in production
# CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com

# Cloud Mode (GitHub App + webhooks) — uncomment ALL three to enable
# DATABASE_URL=postgresql://codewalk:your-very-strong-password-here-20-chars-min@postgres/codewalk
# GITHUB_APP_ID=123456
# GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
# GITHUB_WEBHOOK_SECRET=whsec_xxxxxxxx
# ADMIN_API_KEY=cw_admin_xxxxxxxx

# Embeddings model override
# EMBEDDING_MODEL=jinaai/jina-embeddings-v2-base-code
```

### Step 4: Start Services (First Time)

```bash
cd /opt/codewalk

# Pull and start everything
docker compose up -d

# Watch logs
docker compose logs -f
```

**First startup takes 2-5 minutes** because:
- Postgres initializes its data directory
- Caddy provisions a Let's Encrypt SSL certificate
- The API container downloads the embedding model (~1.5GB)

### Step 5: Verify SSL

```bash
curl -vI https://codewalk.yourdomain.com/health
```

You should see:
- HTTP 200
- `Server: Caddy` header
- Valid TLS certificate from Let's Encrypt

---

## Application Deployment

### GitHub Secrets

Go to **Repo → Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value | Where to get it |
|--------|-------|-----------------|
| `HETZNER_HOST` | `123.45.67.89` | Hetzner Console → Server → IP |
| `HETZNER_USER` | `root` | Your SSH username |
| `HETZNER_SSH_KEY` | Full PEM private key | `cat ~/.ssh/id_rsa` (or your deploy key) |

> **Note:** `GITHUB_TOKEN` is provided automatically by GitHub Actions. No need to create it.

### First Deploy (Manual Trigger)

The workflow triggers on every push to `master`. For the first deploy, push your code:

```bash
git add .
git commit -m "chore: deployment ready"
git push origin master
```

Watch the workflow run: **Repo → Actions → Build & Deploy**

### What the CI/CD Does

1. **Build stage:**
   - Checks out code
   - Logs in to GHCR
   - Builds Docker image with BuildKit cache
   - Tags: `latest` + short SHA
   - Pushes to `ghcr.io/YOUR_ORG/codewalk`

2. **Deploy stage:**
   - SSH into Hetzner server
   - Runs: `docker compose pull codewalk-api`
   - Runs: `docker compose up -d --remove-orphans`
   - Runs: `docker system prune -f` (cleans old images)

---

## GitHub App Setup

Required for **cloud mode** — automatic repo indexing via webhooks.

### 1. Create the GitHub App

1. Go to **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App**
2. Fill in:

| Field | Value |
|-------|-------|
| **GitHub App name** | `codewalk-yourname` (must be globally unique) |
| **Homepage URL** | `http://62.238.42.150` (your Hetzner IP) |
| **Webhook URL** | `http://62.238.42.150/webhooks/github` |
| **Webhook secret** | Generate with `openssl rand -hex 32` |

3. **Permissions:**

| Permission | Access | Why |
|------------|--------|-----|
| **Repository contents** | Read-only | Clone/pull repos |
| **Metadata** | Read-only | List repos in installation |
| **Pull requests** | Read & write | Review PRs, post comments |
| **Issues** | Read & write | Create issues for findings |

4. **Subscribe to events:**
   - ✅ Push
   - ✅ Pull request
   - ✅ Installation
   - ✅ Installation repositories

5. **Where can this GitHub App be installed?**
   - ✅ Any account (for public repos)
   - Or "Only on this account" (for private testing)

6. Click **Create GitHub App**

### 2. Get Credentials

After creation, note these values:

| Credential | Where | Env Var |
|------------|-------|---------|
| **App ID** | Top of app settings page | `GITHUB_APP_ID` |
| **Client ID** | Same page | (not needed for webhooks) |
| **Private Key** | "Private keys" → Generate → download `.pem` | `GITHUB_APP_PRIVATE_KEY` |
| **Webhook Secret** | The secret you set above | `GITHUB_WEBHOOK_SECRET` |

**Private key format:** Paste the entire `.pem` file content (including `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----`) into the env var. It should be a single line with `\n` preserved, or the actual multiline PEM.

### 3. Install the App

1. In your GitHub App settings → **Install App** (left sidebar)
2. Select your account or organization
3. Choose repositories:
   - **All repositories** — Codewalk indexes every repo you push to
   - **Only select repositories** — choose specific repos
4. Click **Install**

### 4. Update Server `.env`

SSH into your server and add the cloud mode variables:

```bash
ssh -i ~/.ssh/hetzner_codewalk root@62.238.42.150
cat >> /opt/codewalk/.env << 'EOF'
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----
...
-----END RSA PRIVATE KEY-----
GITHUB_WEBHOOK_SECRET=your-webhook-secret
ADMIN_API_KEY=$(openssl rand -hex 32)
EOF
cd /opt/codewalk && docker compose restart codewalk-api
```

### 5. Verify Webhook Delivery

1. In your GitHub App settings → **Advanced** → **Recent Deliveries**
2. You should see `ping` events with green ✅
3. If red ❌, check:
   - Hetzner firewall allows port 80
   - Caddy is running: `docker ps`
   - API is healthy: `curl http://localhost/health`

### 6. Test End-to-End

Push code to a repo with the app installed:

```bash
git commit --allow-empty -m "test: trigger codewalk indexing"
git push origin master
```

Check indexing status:
```bash
curl -H "X-Admin-Key: $ADMIN_API_KEY" \
  http://62.238.42.150/admin/repos
```

---

## Post-Deployment Verification

Run these checks immediately after first deploy and after every future deploy.

### Health Checks

```bash
# 1. API health
curl https://codewalk.yourdomain.com/health
# Expected: {"status":"ok"}

# 2. Postgres connectivity
docker compose exec postgres pg_isready -U codewalk
# Expected: accept connections

# 3. All services running
docker compose ps
# Expected: postgres, codewalk-api, caddy all "Up"

# 4. API logs (no errors)
docker compose logs --tail 50 codewalk-api
```

### Security Checks

| Check | Command | Expected Result |
|-------|---------|-----------------|
| **Port 8000 not exposed** | `nmap -p 8000 <HETZNER_IP>` | `closed` or `filtered` |
| **SSL valid** | `curl -vI https://codewalk.yourdomain.com` | TLS 1.3, Let's Encrypt |
| **Rate limiting** | `for i in {1..70}; do curl -s -o /dev/null -w "%{http_code}\n" https://codewalk.yourdomain.com/health; done` | First 60 → `200`, rest → `429` |
| **Webhook size limit** | `dd if=/dev/zero bs=1M count=60 | curl -X POST -H "Content-Type: application/json" --data-binary @- https://codewalk.yourdomain.com/webhooks/github` | `413 Payload Too Large` |
| **CORS headers** | `curl -H "Origin: https://evil.com" -I https://codewalk.yourdomain.com/health` | Either no `Access-Control-Allow-Origin` or reflection only if `*` configured |

### Functional Checks

| Check | How |
|-------|-----|
| **Analyze a repo** | `curl -X POST https://codewalk.yourdomain.com/analyze -H "Content-Type: application/json" -d '{"repo_path":"/var/codewalk/repos/my-repo","index_mode":"full"}'` |
| **Chat** | `curl -X POST https://codewalk.yourdomain.com/chat -H "Content-Type: application/json" -d '{"message":"What does this codebase do?"}'` |
| **Modules list** | `curl https://codewalk.yourdomain.com/modules` |
| **Blast radius** | `curl https://codewalk.yourdomain.com/blast-radius` |

---

## Future Deploys (CI/CD)

After the initial setup, deployment is fully automated.

### Normal Flow

```bash
# Make changes locally
git add .
git commit -m "feat: add new feature"
git push origin master
```

GitHub Actions builds and deploys automatically (~3-5 minutes).

### Monitoring Deploys

```bash
# Watch the deploy in real-time
ssh root@<HETZNER_IP> "cd /opt/codewalk && docker compose logs -f --tail 20"
```

### Manual Deploy (Emergency)

If CI/CD is broken, deploy manually:

```bash
ssh root@<HETZNER_IP>
cd /opt/codewalk
docker compose pull codewalk-api
docker compose up -d --remove-orphans
docker system prune -f
```

---

## Rollback Procedure

If a deploy breaks something:

### Quick Rollback (Docker image)

```bash
ssh root@<HETZNER_IP>
cd /opt/codewalk

# See available images
docker images ghcr.io/gupta29470/codewalk

# Rollback to previous image
docker compose pull codewalk-api  # pulls the previous latest (if you re-tagged)
# OR explicitly:
docker compose down
docker pull ghcr.io/gupta29470/codewalk:<PREVIOUS_SHA>
docker tag ghcr.io/gupta29470/codewalk:<PREVIOUS_SHA> ghcr.io/gupta29470/codewalk:latest
docker compose up -d
```

### Full Rollback (Code + Data)

If you need to roll back code AND database state:

```bash
# Stop everything
docker compose down

# Restore Postgres from backup (if you have one)
# docker exec -i postgres psql -U codewalk < backup.sql

# Re-deploy previous Git commit
git log --oneline -5  # find the good commit
git revert <BAD_COMMIT_SHA>
git push origin master
```

---

## Troubleshooting

### Caddy won't start — "bind: address already in use"

Something is using port 80 or 443:
```bash
ss -tlnp | grep -E ':80|:443'
systemctl stop apache2 nginx  # if installed
```

### SSL certificate fails

- DNS A record must resolve before Caddy starts
- Port 80 must be open for Let's Encrypt HTTP challenge
- Check: `docker compose logs caddy | grep -i error`

### API container keeps restarting

```bash
docker compose logs --tail 100 codewalk-api
```

Common causes:
- Missing `.env` variables
- Postgres not ready yet (add `depends_on` wait)
- DuckDB lock from previous crash: `rm -rf /var/codewalk/repos/*/.codewalk/*.wal`

### Postgres "database does not exist"

```bash
docker compose exec postgres createdb -U codewalk codewalk
```

### Out of Memory (OOM)

If the server freezes during indexing:
- Add swap: `fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`
- Upgrade to CPX31 (8 vCPU, 16GB RAM)
- Or use a smaller embedding model

### Worker not processing jobs (cloud mode)

```bash
docker compose logs --tail 50 codewalk-api | grep worker
```

Common causes:
- `GITHUB_APP_PRIVATE_KEY` formatting (must be one-line with `\n` or raw multi-line)
- `DATABASE_URL` incorrect
- GitHub App not installed on the target repo

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_PASSWORD` | ✅ | — | Postgres superuser password |
| `LLM_PROVIDER` | ✅ | `groq` | `ollama`, `groq`, `openai`, `anthropic`, `gemini`, `openrouter`, `deepseek` |
| `LLM_MODEL` | ✅ | `llama-3.1-70b-versatile` | Model name for chosen provider |
| `GROQ_API_KEY` | ⚠️ | — | Required if `LLM_PROVIDER=groq` |
| `OPENAI_API_KEY` | ⚠️ | — | Required if `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | ⚠️ | — | Required if `LLM_PROVIDER=anthropic` |
| `GOOGLE_API_KEY` | ⚠️ | — | Required if `LLM_PROVIDER=gemini` |
| `OPENROUTER_API_KEY` | ⚠️ | — | Required if `LLM_PROVIDER=openrouter` |
| `DEEPSEEK_API_KEY` | ⚠️ | — | Required if `LLM_PROVIDER=deepseek` |
| `EMBEDDING_MODEL` | ❌ | `jinaai/jina-embeddings-v2-base-code` | HuggingFace model for embeddings |
| `CORS_ORIGINS` | ❌ | `*` | Comma-separated allowed origins |
| `DATABASE_URL` | ❌ | — | Required for **cloud mode** only |
| `GITHUB_APP_ID` | ❌ | — | Required for **cloud mode** only |
| `GITHUB_APP_PRIVATE_KEY` | ❌ | — | Required for **cloud mode** only |
| `GITHUB_WEBHOOK_SECRET` | ❌ | — | Required for **cloud mode** only |
| `ADMIN_API_KEY` | ❌ | — | Required for **cloud mode** only |

---

## File Structure on Server

```
/opt/codewalk/
├── .env                 # Secrets (never commit this)
├── docker-compose.yml   # Service orchestration
├── Caddyfile            # Reverse proxy config
└── data/                # Persistent data (optional volume)

/var/codewalk/
├── repos/               # Cloned repositories
└── <repo-name>/
    └── latest/          # Cloud indexes
```

---

## Security Hardening (Optional)

1. **Fail2ban** for SSH brute-force protection:
   ```bash
   apt install fail2ban
   ```

2. **UFW** firewall (in addition to Hetzner Cloud Firewall):
   ```bash
   ufw default deny incoming
   ufw allow 22/tcp
   ufw allow 80/tcp
   ufw allow 443/tcp
   ufw enable
   ```

3. **Non-root Docker** (optional):
   ```bash
   usermod -aG docker codewalk
   ```

4. **Automated backups**:
   ```bash
   # Add to crontab
   0 3 * * * docker exec postgres pg_dump -U codewalk codewalk > /backup/codewalk-$(date +\%Y\%m\%d).sql
   ```

---

*Last updated: 2025-06-09*
