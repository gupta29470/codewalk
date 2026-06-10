CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Repositories registered via GitHub App or manual admin registration
CREATE TABLE IF NOT EXISTS repos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       TEXT UNIQUE NOT NULL,           -- owner/repo
    name            TEXT NOT NULL,                   -- repo name only
    owner           TEXT NOT NULL,                   -- owner/org name
    clone_url       TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT 'master',
    installation_id TEXT NOT NULL DEFAULT '',
    repo_token      TEXT NOT NULL DEFAULT '',
    last_indexed_sha VARCHAR(40) DEFAULT NULL,
    index_status    VARCHAR(20) DEFAULT 'pending',   -- pending | indexing | ready | failed
    storage_path    TEXT,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_repos_full_name ON repos(full_name);
CREATE INDEX IF NOT EXISTS idx_repos_installation ON repos(installation_id);
CREATE INDEX IF NOT EXISTS idx_repos_status ON repos(index_status);

-- Indexing jobs (legacy + new incremental indexing tracking)
CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_name       TEXT NOT NULL REFERENCES repos(full_name),
    commit_sha      TEXT NOT NULL,
    commit_message  TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued | running | done | failed
    error           TEXT,
    queued_at       TIMESTAMP DEFAULT NOW(),
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_repo     ON jobs(repo_name);
CREATE INDEX IF NOT EXISTS idx_jobs_queued   ON jobs(queued_at) WHERE status='queued';

-- Webhook delivery log (for debugging / replay)
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      TEXT NOT NULL,                   -- push | installation | ping | etc
    delivery_id     TEXT,                            -- X-GitHub-Delivery header
    repo_full_name  TEXT,
    commit_sha      TEXT,
    payload_size    INTEGER,
    status          TEXT DEFAULT 'received',         -- received | processed | failed | ignored
    error           TEXT,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhooks_repo ON webhook_deliveries(repo_full_name);
CREATE INDEX IF NOT EXISTS idx_webhooks_delivery ON webhook_deliveries(delivery_id);
