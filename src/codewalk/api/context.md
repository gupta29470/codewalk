# `src/codewalk/api/` — FastAPI Server

This package exposes Codewalk as an HTTP API and manages shared in-memory state for the API process.

## Modules

| File | Role |
|------|------|
| `main.py` | FastAPI app, middleware (rate limit, cloud-mode block, staleness), and all REST endpoints: `/analyze`, `/analyze/stream`, `/chat`, `/chat/stream`, `/chat/approve`, `/research`, `/overview`, `/modules`, `/blast-radius`, `/reading-order`, `/execution-flow`, `/refresh`, `/incremental-reindex`, `/review`, `/review/file`, `/review/guidelines`, `/review/apply`, `/tools/static-analysis`, `/tools/run-tests`, `/semantic-search`, `/rag/expand-query`, `/rag/rerank`, `/rag/symbol-lookup`, `/docs/index`, `/docs/search`, `/docs/ask`, `/cycles`, `/architecture`, `/version`, `/staleness`, `/index-status`, `/health`. The agent's `search_codebase` tool expands each query into 1–3 parallel search angles. |
| `state.py` | Module-level shared state: `VectorStore`, `GraphStore`, `GraphRuntime`, agent, modules, files, Postgres helper (`_PgHelper`). |
| `cloud.py` | GitHub webhook receiver, catch-up/index worker, atomic index publishing, cloud admin routes. |
| `models.py` | Pydantic request/response models. |

## Data flow

```
POST /analyze
    ↓
state.set_repo_path() + analyze_or_reindex_index(mode=auto|reindex|full)
    ↓
{no index} → full_index_parallel → build_full_analysis
{complete index} → load_scoped_analysis
{partial index} → status="behind" warning + message
{reindex/full} → incremental_reindex / full_index_parallel, then build_full_analysis
    ↓
query/review/chat endpoints read from state
```

## Connections

- `main.py` imports `pipeline.*`, `state`, `rag/`, `review/`, `agent/`, `generation/`, `analysis/`, and `embeddings/`.
- `state.py` imports `pipeline.build_full_analysis`, `agent.create_agent`, `analysis.*`, `graph.*`, `embeddings.*`, `doc_knowledge.*`.
- `cloud.py` imports `api/state`, `pipeline`, `worker/github_app`, and uses Postgres via `state.get_db()`.
- Newer RAG endpoints (`/rag/expand-query`, `/rag/rerank`, `/rag/symbol-lookup`) and tool endpoints (`/tools/static-analysis`, `/tools/run-tests`) use request/response models in `models.py`, including `ExpandQueryRequest/Response`, `RerankRequest/Response`, `SymbolLookupRequest/Response`, `StaticAnalysisRequest/Response`, and `TestRunRequest/Response`.

## Recent fixes

- Global exception handler now passes through `HTTPException` / `RequestValidationError`; every endpoint also re-raises `HTTPException` before falling back to generic 500 handling.
- In-memory rate limiter is now protected by `asyncio.Lock()` so concurrent async requests cannot race on the per-IP window.
- Cloud DB connections are thread-local; webhook indexing threads get their own `psycopg2` connection.
- `state.load_scoped_analysis()` repopulates the DuckDB `files` table when it is empty (e.g., after a schema migration) so chunk backfill from ChromaDB does not violate foreign-key constraints.
- `/admin/index` now offloads git clone/pull and incremental indexing to `asyncio.to_thread()` so the event loop is not blocked.
- Cloud `_clone_or_pull_repo()` now checks git return codes and surfaces clone/pull failures instead of silently continuing.
- `/review/apply` now returns the declared `ApplyFixesResponse` model with a `failed` array, so partial failures are visible to the frontend.
- `/docs/index` maps the internal store result keys to the frontend API contract (`files_indexed`, `chunks_created`).
- Requests that resolve to a repo different from the currently loaded index are rejected instead of silently using a stale store.
- Docs endpoints accept `repo_path`; `/review/guidelines` indexes docs from the requested `docs_path`.
- Review guidelines are resolved from `codewalk.yaml` (`code_guidelines` path, or `code_guidelines.*` inside `docs_path`).
- `/semantic-search` uses the repo's stored collection and the shared `state.get_store()`.
- `/analyze` (sync + stream) refreshes the `VectorStore` handle after indexing so `state.get_store()` always points at live Chroma collections.
- `AnalyzeResponse` now includes a `message` field (used for the `behind` warning).
- `chat_approve` preserves `HTTPException` and maps `RuntimeError` to 400.
- Catch-up indexing inserts and updates a `jobs` row so `/admin/repos` shows the current run.

## Notes

- Incremental reindex updates Chroma incrementally, then fully rebuilds DuckDB and `knowledge-graph.json` from all Chroma chunks so the `chunks` table stays consistent.
- `POST /analyze` with `index_mode="auto"` no longer silently resumes partial indexes; it returns `status="behind"` and a message telling the caller to use `POST /incremental-reindex` or `POST /analyze` with `index_mode="reindex"`.
- Agent chat (`/chat`, `/chat/stream`) routes use `agent/tools.py`, where `search_codebase` internally expands the user query into 1–3 complementary retrieval angles and synthesizes the results.
- `POST /docs/ask` expands the question into 1–3 complementary retrieval angles via `expand_query()`, merges deduplicated doc chunks via `DocStore.multi_search()`, and synthesizes the answer.
