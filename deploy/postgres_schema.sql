CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS repos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT UNIQUE NOT NULL,
    github_url      TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT 'main',
    installation_id TEXT NOT NULL DEFAULT '',
    repo_token      TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_name       TEXT NOT NULL REFERENCES repos(name),
    commit_sha      TEXT NOT NULL,
    commit_message  TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'queued',
    error           TEXT,
    queued_at       TIMESTAMP DEFAULT NOW(),
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status   ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_repo     ON jobs(repo_name);
CREATE INDEX IF NOT EXISTS idx_jobs_queued   ON jobs(queued_at) WHERE status='queued';
