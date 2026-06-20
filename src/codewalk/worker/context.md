# `src/codewalk/worker/` — Background Cloud Worker

This package runs the standalone cloud indexing worker that polls Postgres jobs, clones repos, builds indexes, and atomically publishes them.

## Modules

| File | Role |
|------|------|
| `indexer.py` | `worker_loop()` polls `jobs` table; `build_index()` clones repo, runs `full_index_parallel` + `build_full_analysis`, and atomically swaps the result into `latest/`. |
| `github_app.py` | GitHub App installation token retrieval and private-key handling. |
| `atomic_store.py` | `atomic_swap()` for replacing an index directory safely. |

## Data flow

```
Postgres jobs table (queued)
    ↓
worker_loop SELECT FOR UPDATE SKIP LOCKED
    ↓
build_index():
  shallow clone → full_index_parallel → build_full_analysis → write_manifest → atomic_swap
    ↓
update job status to done/failed
```

## Connections

- Uses `pipeline.py`, `team_config.py`, `graph/graph_store.py`, `embeddings/vector_store.py`.
- The API server's `api/cloud.py` provides an alternate webhook-driven path; `worker/indexer.py` is the polling worker.
- Uses its own Postgres connection (thread-safe, unlike `api/cloud.py` webhook threads).

## Known issues

- `build_index()` uses `team_scan_directory()` for the analysis pass, which applies the core `file_filter.py` safety net plus `codewalk.yaml` excludes. `.codewalk/` and other generated directories are handled by the core net.
