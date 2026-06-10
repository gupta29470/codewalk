# ─── Codewalk API Dockerfile ─────────────────────────────────────────
# Multi-stage build for production deployment on Hetzner / any VPS.

FROM python:3.11-slim-bookworm AS builder

# Install build dependencies (for tree-sitter grammars, numpy, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ─── Runtime stage ───────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

# Runtime deps: git (for clone in worker), ffmpeg (for audio processing if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r codewalk && useradd -r -g codewalk codewalk

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/codewalk/.local
ENV PATH=/home/codewalk/.local/bin:$PATH

# Copy application code
COPY src/ ./src/
COPY deploy/pyproject.toml .

# Create data dirs and set ownership
RUN mkdir -p /var/codewalk /app/.codewalk && \
    chown -R codewalk:codewalk /var/codewalk /app /home/codewalk

USER codewalk

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

EXPOSE 8000

CMD ["uvicorn", "src.codewalk.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
