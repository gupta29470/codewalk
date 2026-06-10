# Codewalk — Complete Setup Guide (A to Z)

> **Goal**: Get Codewalk running on Hetzner with automated CI/CD, public/private repo support, and cloud auto-indexing.
> **Time**: ~60 minutes first time, ~5 minutes after that.

---

## Table of Contents

1. [What You're Building](#1-what-youre-building)
2. [Prerequisites](#2-prerequisites)
3. [Part A: GitHub Repository Setup](#3-part-a-github-repository-setup)
4. [Part B: GitHub Container Registry (GHCR)](#4-part-b-github-container-registry-ghcr)
5. [Part C: GitHub Actions CI/CD](#5-part-c-github-actions-cicd)
6. [Part D: Hetzner Server Setup](#6-part-d-hetzner-server-setup)
7. [Part E: DNS & Domain](#7-part-e-dns--domain)
8. [Part F: Server Environment & Secrets](#8-part-f-server-environment--secrets)
9. [Part G: Docker Compose Deployment](#9-part-g-docker-compose-deployment)
10. [Part H: GitHub App (Cloud Auto-Indexing)](#10-part-h-github-app-cloud-auto-indexing)
11. [Part I: Webhook Flow (How It Works)](#11-part-i-webhook-flow-how-it-works)
12. [Part J: Admin Operations](#12-part-j-admin-operations)
13. [Part K: Verification Checklist](#13-part-k-verification-checklist)
14. [Part L: Troubleshooting](#14-part-l-troubleshooting)
15. [Part M: Rollback Procedure](#15-part-m-rollback-procedure)
16. [Part N: Security Hardening](#16-part-n-security-hardening)
17. [Architecture Diagram](#17-architecture-diagram)

---

## 1. What You're Building

```
┌─────────────────────────────────────────────────────────────────────┐
│                         GITHUB (Cloud)                               │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐   │
│  │   Your Repo  │────▶│ GitHub App  │────▶│  Webhook POST       │   │
│  │  (public/   │     │ (auto-index)│     │  /webhooks/github   │   │
│  │   private)  │     └─────────────┘     └──────────┬──────────┘   │
│  └─────────────┘                                    │               │
│           │                                         │               │
│           ▼                                         ▼               │
│  ┌─────────────┐                          ┌─────────────────────┐   │
│  │ GitHub      │                          │   Codewalk API      │   │
│  │ Actions     │                          │   (Hetzner)         │   │
│  │ (build +   │                          │                     │   │
│  │  deploy)    │                          │  • Auto-register    │   │
│  └──────┬──────┘                          │  • Incremental index│   │
│         │                                 │  • Vector search    │   │
│         │ docker push                     │  • Coding assistant │   │
│         ▼                                 └─────────────────────┘   │
│  ┌─────────────┐                                                     │
│  │ GHCR        │◄──────────────────────────── docker pull            │
│  │ (registry)  │                                                     │
│  └─────────────┘                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTPS
                                    ▼
                           ┌─────────────────┐
                           │  HETZNER CPX21  │
                           │  (4vCPU/8GB)    │
                           │                 │
                           │  ┌───────────┐  │
                           │  │  Caddy    │  │  ← HTTPS + reverse proxy
                           │  │  (:443)   │  │
                           │  └─────┬─────┘  │
                           │        │        │
                           │  ┌─────┴─────┐  │
                           │  │  Codewalk │  │  ← FastAPI app
                           │  │  (:8000)  │  │
                           │  └─────┬─────┘  │
                           │        │        │
                           │  ┌─────┴─────┐  │
                           │  │  Postgres │  │  ← Vector DB + metadata
                           │  │  (:5432)  │  │
                           │  └───────────┘  │
                           └─────────────────┘
```

---

## 2. Prerequisites

| Item | Required | Notes |
|------|----------|-------|
| **GitHub account** | ✅ Yes | For repo, Actions, GHCR, App |
| **Domain name** | ✅ Yes | e.g., `codewalk.cloud` — needed for HTTPS webhooks |
| **Hetzner account** | ✅ Yes | Cloud VPS provider (DigitalOcean/AWS work too) |
| **SSH key pair** | ✅ Yes | Generate: `ssh-keygen -t ed25519 -C "codewalk" -f ~/.ssh/hetzner_codewalk` |
| **Credit card** | ✅ Yes | Hetzner ~€6/month, domain ~€10/year |
| **Git** | ✅ Yes | Local machine |
| **Docker Desktop** | ⚪ Optional | For local testing only |

---

## 3. Part A: GitHub Repository Setup

### 3.1 Create the Repository

1. Go to `https://github.com/new`
2. **Repository name**: `codewalk`
3. **Visibility**: 
   - **Public** → Easier (no GHCR auth needed on server)
   - **Private** → You MUST configure GHCR auth (see Part B)
4. **Initialize with README**: ✅ Yes
5. **Add .gitignore**: Python
6. Click **Create repository**

### 3.2 Push Your Local Code

```bash
# In your local codewalk project directory
git remote add origin https://github.com/YOUR_USERNAME/codewalk.git
git branch -M master
git push -u origin master
```

### 3.3 Branch Name

> **CRITICAL**: This project uses `master`, NOT `main`. All scripts reference `master`.

If your repo defaulted to `main`, change it:
```bash
git branch -M master
git push -u origin master
# In GitHub UI: Settings → Branches → Default branch → master
```

---

## 4. Part B: GitHub Container Registry (GHCR)

### 4.1 What is GHCR?

GitHub Container Registry stores your Docker images. GitHub Actions builds → pushes here → server pulls from here.

### 4.2 For Public Repositories

1. Go to `https://github.com/YOUR_USERNAME/codewalk/pkgs/container/codewalk`
2. Click **Package settings**
3. Under **Danger Zone** → **Change visibility**
4. Select **Public** → Confirm with your password
5. ✅ Done — server can pull without authentication

### 4.3 For Private Repositories (Extra Steps)

If you keep the repo private, the server needs a GitHub Personal Access Token (PAT) to pull images:

1. **Create PAT**:
   - Go to `https://github.com/settings/tokens/new`
   - **Note**: `codewalk-server-pull`
   - **Expiration**: 90 days (or custom)
   - **Scopes**: ✅ `read:packages`
   - Click **Generate token**
   - **Copy the token immediately** (you can't see it again)

2. **Server-side GHCR login**:
   ```bash
   ssh root@YOUR_SERVER_IP
   echo "YOUR_PAT" | docker login ghcr.io -u YOUR_USERNAME --password-stdin
   ```
   This creates `~/.docker/config.json` with encrypted credentials.

3. **Add PAT to server .env**:
   ```
   GITHUB_PAT=ghp_xxxxxxxxxxxxxxxxxxxx
   ```

4. **Deploy script already handles this** — it tries GHCR pull first, falls back to local build if auth fails.

---

## 5. Part C: GitHub Actions CI/CD

### 5.1 How It Works

1. You push to `master`
2. GitHub Actions triggers
3. Builds Docker image with tags: `latest` + `sha-abc1234`
4. Pushes to GHCR
5. SSHs to Hetzner server
6. Runs deploy script on server
7. Deploy script pulls new image + restarts containers

### 5.2 Required Secrets

Go to: `https://github.com/YOUR_USERNAME/codewalk/settings/secrets/actions`

Add these **Repository secrets** (not environment secrets):

| Secret Name | Value | How to Get |
|-------------|-------|------------|
| `HETZNER_HOST` | `62.238.42.150` | Your server's public IP |
| `HETZNER_USER` | `root` | SSH username (usually root on Hetzner) |
| `HETZNER_SSH_KEY` | Full private key | `cat ~/.ssh/hetzner_codewalk` — paste ALL content |

> ⚠️ **Paste the ENTIRE private key**, including `-----BEGIN OPENSSH PRIVATE KEY-----` and `-----END OPENSSH PRIVATE KEY-----` lines.

### 5.3 Verify the Workflow File

The workflow file is at `.github/workflows/deploy.yml`. It should look like:

```yaml
name: Build & Deploy

on:
  push:
    branches: [master]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      
      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,prefix=sha-,format=short
            type=raw,value=latest
      
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max

  deploy:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/master'
    steps:
      - name: Deploy to Hetzner via SSH
        uses: appleboy/ssh-action@v1.0.3
        env:
          DEPLOY_SHA: ${{ github.sha }}
        with:
          host: ${{ secrets.HETZNER_HOST }}
          username: ${{ secrets.HETZNER_USER }}
          key: ${{ secrets.HETZNER_SSH_KEY }}
          script_stop: true
          script: |
            # Update source code first (in case we need local build fallback)
            if [ -d /opt/codewalk-src/.git ]; then
              cd /opt/codewalk-src && git pull origin master
            else
              git clone https://github.com/gupta29470/codewalk.git /opt/codewalk-src
            fi

            # Ensure deploy script is latest
            cp /opt/codewalk-src/deploy/deploy-server.sh /opt/codewalk/deploy-server.sh
            chmod +x /opt/codewalk/deploy-server.sh

            # Run deploy with SHA from GitHub Actions (first 7 chars)
            SHORT_SHA="${DEPLOY_SHA:0:7}"
            echo "Deploying SHA: $SHORT_SHA"
            DEPLOY_SHA="$SHORT_SHA" /opt/codewalk/deploy-server.sh

      - name: Deployment summary
        if: always()
        run: |
          echo "Deploy SHA: ${{ github.sha }}"
          echo "Status: ${{ job.status }}"
```

### 5.4 Test the Pipeline

After setting up secrets:

1. Make a small commit to `master` (e.g., edit README)
2. Push: `git push origin master`
3. Go to `https://github.com/YOUR_USERNAME/codewalk/actions`
4. Watch the "Build & Deploy" workflow run
5. Green checkmarks = success

---

## 6. Part D: Hetzner Server Setup

### 6.1 Create Server

1. Go to `https://console.hetzner.cloud/projects`
2. Click **Add Server**
3. **Location**: Frankfurt (or closest to your users)
4. **Image**: Ubuntu 26.04 LTS
5. **Type**: CPX21 (4 vCPU, 8 GB RAM, 80 GB NVMe) — minimum for AI features
6. **Networking**: IPv4 + IPv6
7. **SSH Key**: Add your public key (`cat ~/.ssh/hetzner_codewalk.pub`)
8. **Name**: `codewalk-prod`
9. Click **Create & Buy**

### 6.2 Wait for Provisioning

Takes ~1 minute. You'll get an email with the server IP.

### 6.3 Initial Server Setup

SSH in and run the setup script:

```bash
# From your local machine
ssh -i ~/.ssh/hetzner_codewalk root@YOUR_SERVER_IP

# On the server, run:
apt-get update && apt-get install -y \
  apt-transport-https ca-certificates curl gnupg \
  git python3 python3-pip

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

# Install Docker Compose plugin
docker compose version  # Should show v5.x+

# Verify
docker --version   # 29.x+
docker compose version  # 2.x+
```

### 6.4 Create Directory Structure

```bash
mkdir -p /opt/codewalk /opt/codewalk-src
```

---

## 7. Part E: DNS & Domain

### 7.1 Point Domain to Server

At your domain registrar (Namecheap, Cloudflare, GoDaddy, etc.):

| Type | Host | Value | TTL |
|------|------|-------|-----|
| A | `@` | `YOUR_SERVER_IP` | 300 |
| A | `www` | `YOUR_SERVER_IP` | 300 |
| A | `api` | `YOUR_SERVER_IP` | 300 |

Examples:
- `codewalk.cloud` → server IP
- `api.codewalk.cloud` → server IP

### 7.2 Wait for Propagation

```bash
# Check if DNS is resolving
dig +short codewalk.cloud
dig +short api.codewalk.cloud
```

Should return your server IP within 5–30 minutes.

---

## 8. Part F: Server Environment & Secrets

### 8.1 Create .env File

On the server:

```bash
nano /opt/codewalk/.env
```

Paste this (replace ALL values):

```env
# ============================================
# CORE CONFIGURATION
# ============================================
LOG_LEVEL=INFO
MAX_WORKERS=4

# ============================================
# DATABASE (Postgres inside Docker)
# ============================================
DATABASE_URL=postgresql://codewalk:codewalk@postgres:5432/codewalk
DB_HOST=postgres
DB_PORT=5432
DB_NAME=codewalk
DB_USER=codewalk
DB_PASSWORD=codewalk

# ============================================
# API SERVER
# ============================================
API_HOST=0.0.0.0
API_PORT=8000
RATE_LIMIT_REQUESTS=1000
RATE_LIMIT_WINDOW=60

# ============================================
# AI / LLM PROVIDERS
# ============================================
# Pick ONE primary provider:

# Option A: OpenAI (recommended for quality)
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENAI_MODEL=gpt-4o-mini

# Option B: Ollama (free, runs locally — NOT in Docker)
# OLLAMA_HOST=http://host.docker.internal:11434
# OLLAMA_MODEL=llama3.1:8b

# ============================================
# EMBEDDINGS (vector search)
# ============================================
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small

# ============================================
# CODING ASSISTANT (code explanation, suggestions)
# ============================================
CODING_ASSISTANT_ENABLED=true
CODING_ASSISTANT_MODEL=gpt-4o-mini
CODING_ASSISTANT_MAX_TOKENS=2000

# ============================================
# GITHUB APP (Cloud Auto-Indexing)
# ============================================
# Only needed for auto-indexing on git push
# See Part H for setup

# GITHUB_APP_ID=1234567
# GITHUB_APP_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----
# MIIEpAIBAAKCAQEA...
# ...
# -----END RSA PRIVATE KEY-----
# GITHUB_WEBHOOK_SECRET=your_webhook_secret_here

# ============================================
# ADMIN API KEY (for manual repo registration)
# ============================================
ADMIN_API_KEY=your-secure-random-key-here-change-me

# ============================================
# FRONTEND
# ============================================
FRONTEND_URL=https://codewalk.cloud

# ============================================
# GOOGLE / AUTH (optional)
# ============================================
# GOOGLE_CLIENT_ID=
# GOOGLE_CLIENT_SECRET=
```

### 8.2 Generate Secure Keys

```bash
# Admin API key (random 32 chars)
openssl rand -base64 32

# Webhook secret (random 32 chars)
openssl rand -hex 32
```

---

## 9. Part G: Docker Compose Deployment

### 9.1 Copy Compose File

The `deploy/docker-compose.yml` should already be in your repo. On the server:

```bash
cd /opt/codewalk
curl -O https://raw.githubusercontent.com/YOUR_USERNAME/codewalk/master/deploy/docker-compose.yml
```

Or clone the repo:
```bash
cd /opt/codewalk-src
git clone https://github.com/YOUR_USERNAME/codewalk.git .
```

### 9.2 First Deploy (Manual)

Since GHCR might be empty on first run, build locally:

```bash
cd /opt/codewalk-src

# Copy compose file
cp deploy/docker-compose.yml /opt/codewalk/docker-compose.yml
cp deploy/Caddyfile /opt/codewalk/Caddyfile 2>/dev/null || true

# Build and start
cd /opt/codewalk
docker compose up -d --build
```

### 9.3 Verify Deployment

```bash
# Check containers
docker ps

# Check logs
docker compose logs -f codewalk-api

# Test health endpoint
curl http://localhost:8000/health
# Expected: {"status":"ok"}

# Test from external
curl http://YOUR_SERVER_IP:8000/health
```

### 9.4 Set Up Deploy Script

```bash
cp /opt/codewalk-src/deploy/deploy-server.sh /opt/codewalk/deploy-server.sh
chmod +x /opt/codewalk/deploy-server.sh
```

### 9.5 Set Up Rollback Script

```bash
cp /opt/codewalk-src/deploy/rollback.sh /opt/codewalk/rollback.sh
chmod +x /opt/codewalk/rollback.sh
```

---

## 10. Part H: GitHub App (Cloud Auto-Indexing)

### 10.1 What Is This?

A GitHub App that:
- Watches your repositories for `git push` events
- Sends webhooks to your Codewalk server
- Server auto-indexes the changed code
- No manual action needed after initial setup

### 10.2 Create the GitHub App

1. Go to `https://github.com/settings/apps/new`
2. **GitHub App name**: `Codewalk Cloud` (must be unique across GitHub)
3. **Homepage URL**: `https://codewalk.cloud`
4. **Callback URL**: (leave blank unless using OAuth)
5. **Webhook URL**: `https://api.codewalk.cloud/webhooks/github`
   - For testing: `http://YOUR_SERVER_IP:8000/webhooks/github`
   - Must be HTTPS for production (GitHub requires it)
6. **Webhook secret**: Generate with `openssl rand -hex 32`
7. **Permissions**:

   | Permission | Access | Why |
   |------------|--------|-----|
   | Contents | Read-only | Clone repos |
   | Metadata | Read-only | Auto-granted, repo info |
   | Commit statuses | Read-only | Check CI status |

8. **Subscribe to events**:
   - ✅ Push
   - ✅ Pull request (optional)
   - ✅ Installation (optional)

9. **Where can this GitHub App be installed?**: Any account
10. Click **Create GitHub App**

### 10.3 Generate Private Key

1. On your app's settings page, scroll to **Private keys**
2. Click **Generate a private key**
3. A `.pem` file downloads automatically
4. **Keep this file secure** — it cannot be downloaded again

### 10.4 Get App ID

On the app settings page, you'll see:
- **App ID**: e.g., `1234567` — note this number

### 10.5 Install the App

1. On your app page, click **Install App** (left sidebar)
2. Select your account/organization
3. Choose repositories:
   - **All repositories** (recommended)
   - OR select specific repos
4. Click **Install**

### 10.6 Update Server Environment

Add to `/opt/codewalk/.env`:

```env
GITHUB_APP_ID=1234567
GITHUB_APP_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
...
-----END RSA PRIVATE KEY-----
GITHUB_WEBHOOK_SECRET=your_webhook_secret_here
```

For the private key, you can either:
- Paste the entire key (with newlines)
- Or base64-encode it: `base64 -w0 private-key.pem` and paste the result

### 10.7 Restart Server

```bash
cd /opt/codewalk
docker compose up -d
```

---

## 11. Part I: Webhook Flow (How It Works)

### 11.1 On Every Git Push

```
Developer pushes code
        │
        ▼
GitHub App receives push event
        │
        ▼
POST https://api.codewalk.cloud/webhooks/github
  Headers:
    X-GitHub-Event: push
    X-GitHub-Delivery: <uuid>
    X-Hub-Signature-256: sha256=<hmac>
  Body:
    {
      "repository": {
        "full_name": "user/repo",
        "clone_url": "https://github.com/user/repo.git"
      },
      "ref": "refs/heads/master",
      "after": "abc123..."   ← new commit SHA
    }
        │
        ▼
Codewalk Server:
  1. Verify HMAC signature (webhook secret)
  2. Check if repo is registered
     - If NO → Auto-register (insert into DB)
     - If YES → Check SHA
  3. If SHA changed from last_indexed_sha:
     - git pull (or git clone if first time)
     - incremental_reindex() — only changed files
     - Update last_indexed_sha
  4. Return 200 OK to GitHub
        │
        ▼
Code is now searchable in Codewalk!
```

### 11.2 Webhook Delivery Log

Every webhook is saved to the `webhook_deliveries` table:

```sql
SELECT event_type, repo_full_name, status, processed_at
FROM webhook_deliveries
ORDER BY processed_at DESC
LIMIT 10;
```

---

## 12. Part J: Admin Operations

### 12.1 Manual Repo Registration

If you don't use the GitHub App, register repos manually:

```bash
curl -X POST https://api.codewalk.cloud/admin/register \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/USER/REPO.git",
    "branch": "master"
  }'
```

### 12.2 List Registered Repos

```bash
curl -X POST https://api.codewalk.cloud/admin/repos \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

### 12.3 Trigger Manual Index

```bash
curl -X POST https://api.codewalk.cloud/admin/index \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "repo_name": "USER/REPO",
    "branch": "master"
  }'
```

### 12.4 View Jobs

```bash
curl -X POST https://api.codewalk.cloud/admin/jobs \
  -H "Authorization: Bearer YOUR_ADMIN_API_KEY"
```

---

## 13. Part K: Verification Checklist

After setup, verify everything:

```bash
# 1. Server is reachable
ping YOUR_SERVER_IP

# 2. Docker containers running
ssh root@YOUR_SERVER_IP "docker ps"
# Should show: codewalk-api, codewalk-postgres, codewalk-caddy

# 3. Health check passes
curl https://api.codewalk.cloud/health
# Expected: {"status":"ok"}

# 4. HTTPS works (no certificate warnings)
curl -I https://api.codewalk.cloud/health
# Expected: HTTP/2 200

# 5. Database is accessible
ssh root@YOUR_SERVER_IP "docker exec codewalk-postgres-1 psql -U codewalk -d codewalk -c '\dt'"
# Expected: repos, jobs, webhook_deliveries tables

# 6. GitHub Actions triggers on push
git commit --allow-empty -m "test: verify CI/CD"
git push origin master
# Check: https://github.com/YOUR_USERNAME/codewalk/actions

# 7. Webhook arrives after push (if GitHub App installed)
# Check server logs:
ssh root@YOUR_SERVER_IP "docker compose logs codewalk-api --tail=50"
# Should show: "Processing push event for USER/REPO"

# 8. Repo appears in database
ssh root@YOUR_SERVER_IP "docker exec -i codewalk-postgres-1 psql -U codewalk -d codewalk -c 'SELECT full_name, index_status FROM repos;'"
```

---

## 14. Part L: Troubleshooting

### 14.1 GitHub Actions Build Fails

**Symptom**: Red X on build step  
**Check**:
```bash
# Go to Actions tab → click failed run → expand logs
# Common issues:
# - Missing GHCR write permission → Add packages: write permission
# - Dockerfile error → Test locally: docker build .
```

### 14.2 Deploy Step Fails

**Symptom**: Build passes, deploy fails  
**Check**:
```bash
# 1. SSH key is correct
cat ~/.ssh/hetzner_codewalk | head -1
# Should start with: -----BEGIN OPENSSH PRIVATE KEY-----

# 2. Secrets are set in GitHub UI
# Settings → Secrets and variables → Actions

# 3. Server is reachable
ssh -i ~/.ssh/hetzner_codewalk root@YOUR_SERVER_IP "echo ok"
```

### 14.3 Server Can't Pull from GHCR

**Symptom**: `docker pull` fails with "unauthorized"  
**Fix**:
```bash
# If repo is private, login:
echo "YOUR_PAT" | docker login ghcr.io -u YOUR_USERNAME --password-stdin

# If repo is public, make GHCR package public:
# https://github.com/YOUR_USERNAME/codewalk/pkgs/container/codewalk
# → Package settings → Public

# Fallback: deploy script automatically falls back to local build
```

### 14.4 Health Check Fails

**Symptom**: Containers restart in a loop  
**Check**:
```bash
docker compose logs codewalk-api --tail=100

# Common causes:
# - Missing .env file → cp env.example .env
# - Database not ready → Wait for postgres to start first
# - Port conflict → Kill process on :8000
# - Out of memory → Upgrade server or reduce MAX_WORKERS
```

### 14.5 Webhook Not Received

**Symptom**: Push to repo, no index triggered  
**Check**:
```bash
# 1. Is the app installed on the repo?
# GitHub → Repo → Settings → GitHub Apps → should show "Codewalk Cloud"

# 2. Is webhook URL correct?
# App settings → Webhook URL should be: https://api.codewalk.cloud/webhooks/github

# 3. Check recent deliveries:
# App settings → Advanced → Recent deliveries

# 4. Check server logs:
docker compose logs codewalk-api --tail=50

# 5. Is firewall blocking?
ufw status
# Should allow 80, 443, 8000
```

### 14.6 "Bad credentials" from GitHub

**Symptom**: 401 when cloning repo  
**Fix**:
```bash
# GitHub App private key may be malformed
# Re-download from GitHub App settings
# Or use base64-encoded version in .env
```

### 14.7 Disk Full

**Symptom**: Build fails with "no space left on device"  
**Fix**:
```bash
# Check disk usage
df -h

# Clean Docker
docker system prune -af

# Clean old images
docker image prune -a
```

### 14.8 Rate Limiting Yourself

**Symptom**: 429 Too Many Requests  
**Fix**: Already set `RATE_LIMIT_REQUESTS=1000` in default .env. If still hitting limits:
```bash
# Check logs for IP being rate-limited
docker compose logs codewalk-api | grep "Rate limit"
```

---

## 15. Part M: Rollback Procedure

### 15.1 Automatic Rollback

The deploy script already has auto-rollback: if health check fails after deploy, it reverts to the previous working version.

### 15.2 Manual Rollback

```bash
ssh root@YOUR_SERVER_IP

# Rollback to previous SHA
/opt/codewalk/rollback.sh

# Or rollback to specific SHA
/opt/codewalk/rollback.sh abc1234

# Verify
/opt/codewalk/deploy-server.sh
```

### 15.3 Emergency: Pin to Previous Image

```bash
cd /opt/codewalk

# Edit compose to use specific tag
sed -i 's/ghcr.io\/YOUR_USERNAME\/codewalk:latest/ghcr.io\/YOUR_USERNAME\/codewalk:sha-OLD_SHA/' docker-compose.yml

# Restart
docker compose up -d
```

---

## 16. Part N: Security Hardening

### 16.1 SSH Hardening

```bash
# On server
nano /etc/ssh/sshd_config

# Change:
PermitRootLogin prohibit-password
PasswordAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3

# Restart
systemctl restart sshd
```

### 16.2 Firewall (UFW)

```bash
# Only allow necessary ports
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp   # SSH
ufw allow 80/tcp   # HTTP (Caddy redirects to HTTPS)
ufw allow 443/tcp  # HTTPS
ufw enable
```

> Note: Port 8000 should NOT be exposed externally — only via Caddy reverse proxy.

### 16.3 Secrets Rotation

| Secret | Rotate Every | How |
|--------|-------------|-----|
| GitHub App private key | On suspicion | Regenerate in GitHub App settings |
| Webhook secret | 90 days | Update in GitHub App + server .env |
| Admin API key | 90 days | `openssl rand -base64 32` |
| GHCR PAT (if private) | 90 days | GitHub Settings → Tokens |

### 16.4 HTTPS Only

Caddy automatically handles HTTPS via Let's Encrypt. Never serve the API over plain HTTP in production.

### 16.5 Disable Direct Port 8000 Access (Optional)

```bash
# Only allow Caddy to talk to API
# In docker-compose.yml, change:
ports:
  - "127.0.0.1:8000:8000"  # Only localhost can reach it
# Then Caddy proxies: localhost:8000
```

---

## 17. Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                         DEVELOPER MACHINE                           │
│                                                                     │
│  git push origin master ────────────────────────────────────────────┼──┐
│                                                                     │  │
└────────────────────────────────────────────────────────────────────┘  │
                                                                       │
┌────────────────────────────────────────────────────────────────────┐  │
│                         GITHUB.COM                                  │  │
│                                                                     │  │
│  ┌──────────────────┐        ┌──────────────────┐                  │  │
│  │  Repository      │        │  GitHub App      │                  │  │
│  │  (master branch) │        │  "Codewalk Cloud"│                  │  │
│  │                  │        │                  │                  │  │
│  │  • Source code   │        │  • push events   │                  │  │
│  │  • Dockerfile    │        │  • PR events     │                  │  │
│  │  • compose.yml   │        │  • webhook POST  │                  │  │
│  └────────┬─────────┘        └────────┬─────────┘                  │  │
│           │                           │                            │  │
│           │ triggers                  │ sends webhook              │  │
│           ▼                           ▼                            │  │
│  ┌──────────────────┐        ┌──────────────────┐                  │  │
│  │  GitHub Actions  │        │  api.codewalk.   │                  │  │
│  │  .github/        │        │  cloud           │                  │  │
│  │  workflows/      │        │  /webhooks/      │                  │  │
│  │  deploy.yml      │        │  github          │                  │  │
│  │                  │        │                  │                  │  │
│  │  1. Build image  │        │  HMAC verified   │                  │  │
│  │  2. Push to GHCR │        │  Auto-register   │                  │  │
│  │  3. SSH to server│        │  Incremental     │                  │  │
│  │  4. Run deploy   │        │  index           │                  │  │
│  └────────┬─────────┘        └──────────────────┘                  │  │
│           │                                                        │  │
│           │ docker push                                            │  │
│           ▼                                                        │  │
│  ┌──────────────────┐                                             │  │
│  │  GHCR            │◄────────────────────────────────────────────┘  │
│  │  ghcr.io/user/   │         docker pull (deploy script)            │
│  │  codewalk        │◄───────────────────────────────────────────────┘
│  │                  │
│  │  Tags:           │
│  │  • latest        │
│  │  • sha-abc1234   │
│  └──────────────────┘
└────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ docker pull
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│                      HETZNER CLOUD (CPX21)                          │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Caddy (Reverse Proxy)                                       │  │
│  │  Port: 80, 443                                               │  │
│  │                                                              │  │
│  │  • Auto HTTPS (Let's Encrypt)                                │  │
│  │  • codewalk.cloud → frontend                                 │  │
│  │  • api.codewalk.cloud → codewalk-api:8000                    │  │
│  └──────────────────────────┬───────────────────────────────────┘  │
│                             │                                       │
│                             │ reverse proxy                         │
│                             ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Codewalk API (FastAPI + Uvicorn)                            │  │
│  │  Port: 8000 (internal)                                       │  │
│  │                                                              │  │
│  │  • /health         → health check                            │  │
│  │  • /query          → semantic code search                    │  │
│  │  • /explain        → AI code explanation                     │  │
│  │  • /webhooks/github→ GitHub App webhooks                     │  │
│  │  • /admin/*        → repo management                         │  │
│  │                                                              │  │
│  │  Modules:                                                    │  │
│  │  • Vector Search (OpenAI embeddings)                         │  │
│  │  • Code Parser (tree-sitter)                                 │  │
│  │  • Dependency Graph                                          │  │
│  │  • Incremental Indexer                                       │  │
│  └──────────────────────────┬───────────────────────────────────┘  │
│                             │                                       │
│                             │ SQL                                   │
│                             ▼                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  PostgreSQL 16 + pgvector                                    │  │
│  │  Port: 5432 (internal)                                       │  │
│  │                                                              │  │
│  │  Tables:                                                     │  │
│  │  • repos (registered repositories)                           │  │
│  │  • jobs (index jobs status)                                  │  │
│  │  • webhook_deliveries (audit log)                            │  │
│  │  • code_embeddings (vector search)                           │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  Storage:                                                           │
│  • /opt/codewalk/.env              → environment variables          │
│  • /opt/codewalk/docker-compose.yml→ orchestration                  │
│  • /opt/codewalk/deploy-server.sh  → deployment script              │
│  • /opt/codewalk-src/              → git source (local build)       │
│  • /var/lib/docker/volumes/        → postgres data (persistent)     │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

---

## Quick Reference

### All Important URLs

| Purpose | URL |
|---------|-----|
| Your repo | `https://github.com/YOUR_USERNAME/codewalk` |
| GitHub Actions | `https://github.com/YOUR_USERNAME/codewalk/actions` |
| GHCR Package | `https://github.com/YOUR_USERNAME/codewalk/pkgs/container/codewalk` |
| GitHub App settings | `https://github.com/settings/apps/codewalk-cloud` |
| Repo secrets | `https://github.com/YOUR_USERNAME/codewalk/settings/secrets/actions` |
| Server health | `https://api.codewalk.cloud/health` |

### All Important Commands

```bash
# Local development
git push origin master                          # Trigger deploy

# Server management
ssh -i ~/.ssh/hetzner root@YOUR_IP              # SSH to server
docker ps                                       # List containers
docker compose logs -f codewalk-api             # Tail API logs
docker compose up -d                            # Restart all
docker compose down                             # Stop all

# Deploy & Rollback
/opt/codewalk/deploy-server.sh                  # Deploy latest
/opt/codewalk/deploy-server.sh abc1234          # Deploy specific SHA
/opt/codewalk/rollback.sh                       # Rollback to previous

# Database
PGPASSWORD=codewalk psql -h localhost -U codewalk -d codewalk -c "SELECT * FROM repos;"
docker exec -i codewalk-postgres-1 psql -U codewalk -d codewalk -c "\dt"

# Health & Debug
curl https://api.codewalk.cloud/health
curl -X POST https://api.codewalk.cloud/admin/repos -H "Authorization: Bearer YOUR_KEY"
```

---

## Support

If something breaks:
1. Check **Part L: Troubleshooting** above
2. Check server logs: `docker compose logs -f codewalk-api`
3. Check GitHub Actions logs in the web UI
4. Check GitHub App → Advanced → Recent deliveries

---

*Last updated: 2025-01-09*  
*Codewalk v1.0.0*
