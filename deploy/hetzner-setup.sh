#!/bin/bash
# ══════════════════════════════════════════════════════════════════════
#  Hetzner VPS one-time setup script for Codewalk
#  Run this ONCE on a fresh Ubuntu 22.04/24.04 server
# ══════════════════════════════════════════════════════════════════════
set -e

DOMAIN="${1:-codewalk.yourdomain.com}"
EMAIL="${2:-admin@yourdomain.com}"

echo "=== Codewalk Hetzner Setup ==="
echo "Domain: $DOMAIN"
echo "Email:  $EMAIL"
echo ""

# ── 1. Update system ────────────────────────────────────────────────
apt-get update && apt-get upgrade -y

# ── 2. Install Docker + Compose ─────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    apt-get install -y ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# ── 3. Create app directory ─────────────────────────────────────────
mkdir -p /opt/codewalk
cd /opt/codewalk

# ── 4. Create .env file (user must fill in secrets after) ───────────
cat > .env << 'EOF'
# ─── REQUIRED: Database ──────────────────────────────────────────────
POSTGRES_PASSWORD=change-me-strong-password

# ─── REQUIRED: LLM Provider ──────────────────────────────────────────
# Pick ONE: ollama | groq | openai | anthropic | gemini | openrouter | deepseek
LLM_PROVIDER=groq
LLM_MODEL=llama-3.1-70b-versatile

# API keys (fill in the one matching your provider)
GROQ_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=

# ─── OPTIONAL: Cloud Mode ────────────────────────────────────────────
# DATABASE_URL is auto-built from POSTGRES_PASSWORD in docker-compose.yml
# GITHUB_APP_ID=
# GITHUB_APP_PRIVATE_KEY_PATH=/var/codewalk/secrets/codewalk-cloud.private-key.pem
# GITHUB_WEBHOOK_SECRET=
# ADMIN_API_KEY=

# ─── OPTIONAL: CORS ──────────────────────────────────────────────────
# CORS_ORIGINS=https://your-frontend.com,https://app.yourdomain.com
EOF

echo ""
echo "⚠️  ACTION REQUIRED: Edit /opt/codewalk/.env and fill in your secrets"
echo ""

# ── 5. Create docker-compose override ───────────────────────────────
cat > docker-compose.yml << 'EOF'
services:
  postgres:
    image: postgres:16-alpine
    volumes:
      - pg_data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: codewalk
      POSTGRES_USER: codewalk
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    restart: unless-stopped

  codewalk-api:
    image: ghcr.io/gupta29470/codewalk:latest
    command: uvicorn src.codewalk.api.main:app --host 0.0.0.0 --port 8000 --workers 1
    ports:
      - "8000:8000"
    volumes:
      - /var/codewalk:/var/codewalk
      - /root/.cache/huggingface:/root/.cache/huggingface
    environment:
      - REPO_PATH=/var/codewalk/repos
      - LLM_PROVIDER=${LLM_PROVIDER:-groq}
      - LLM_MODEL=${LLM_MODEL:-llama-3.1-70b-versatile}
      - EMBEDDING_MODEL=${EMBEDDING_MODEL:-jinaai/jina-code-embeddings-1.5b}
      - GROQ_API_KEY=${GROQ_API_KEY:-}
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
      - GOOGLE_API_KEY=${GOOGLE_API_KEY:-}
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
      - DATABASE_URL=postgresql://codewalk:${POSTGRES_PASSWORD}@postgres/codewalk
      - GITHUB_APP_ID=${GITHUB_APP_ID}
      - GITHUB_APP_PRIVATE_KEY_PATH=${GITHUB_APP_PRIVATE_KEY_PATH}
      - GITHUB_WEBHOOK_SECRET=${GITHUB_WEBHOOK_SECRET}
      - ADMIN_API_KEY=${ADMIN_API_KEY}
      - INDEX_STORAGE_PATH=/var/codewalk
      - CORS_ORIGINS=${CORS_ORIGINS:-*}
      - RATE_LIMIT_REQUESTS=${RATE_LIMIT_REQUESTS:-60}
      - RATE_LIMIT_WINDOW=${RATE_LIMIT_WINDOW:-60}
    depends_on:
      - postgres
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  caddy:
    image: caddy:2-alpine
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    ports:
      - "80:80"
      - "443:443"
    depends_on:
      - codewalk-api
    restart: unless-stopped

volumes:
  pg_data:
  caddy_data:
  caddy_config:
EOF

# ── 6. Create Caddyfile ─────────────────────────────────────────────
cat > Caddyfile << EOF
${DOMAIN} {
    reverse_proxy codewalk-api:8000
}
EOF

# ── 7. Index storage (API container runs as uid 999) ─────────────────
mkdir -p /var/codewalk/repos /var/codewalk/indexes /var/codewalk/secrets
chown -R 999:999 /var/codewalk
chmod 755 /var/codewalk

echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit /opt/codewalk/.env with your secrets"
echo "  2. Run: docker compose -f /opt/codewalk/docker-compose.yml up -d"
echo "  3. Point your DNS A record to: $(curl -s ifconfig.me)"
echo "  4. Copy PEM to /var/codewalk/secrets/ and set GITHUB_APP_PRIVATE_KEY_PATH"
echo "  5. Caddy will auto-provision Let's Encrypt SSL"
echo ""
