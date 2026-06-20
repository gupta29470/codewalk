# `src/codewalk/` — Root Package

This is the top-level Python package for Codewalk. It wires together ingestion, embeddings, analysis, graph storage, RAG, review, agent, voice, API, MCP, and cloud worker pieces.

## Key modules at this level

| File | Role |
|------|------|
| `pipeline.py` | Central indexing/analysis orchestrator: `full_index_parallel`, `incremental_reindex`, `build_full_analysis`. Called by CLI, API, MCP, and cloud worker. |
| `cli.py` | Typer CLI entry point (`codewalk analyze`, `codewalk reindex`, `codewalk refresh`, `codewalk generate-config`). |
| `config.py` | Pydantic settings + `get_llm()` factory. Reads env vars for provider, model, embedding model, API keys, CORS, exclude paths. |
| `team_config.py` | Loads `codewalk.yaml` (`indexing.include`, `indexing.exclude`, `branches`, `guidelines_path`, `docs_path`) and provides `team_scan_directory`. |
| `staleness.py` | Compares local `.codewalk/manifest.json` against cloud manifest; version bump banners. |
| `errors.py` | Error classification helper used by the API global exception handler. |
| `log.py` | Project-wide logging helper. |

## Data flow through the root

```
ingestion/ (scan + filter)
    ↓
embeddings/ (chunk + embed + ChromaDB store)
    ↓
analysis/ (deps + modules)
    ↓
graph/ (DuckDB + igraph runtime)
    ↓
rag/, query/, review/, agent/, generation/ (consumers)
```

## Entry points that start here

- **CLI:** `python -m src.codewalk.cli`
- **API server:** `src.codewalk.api.main:app`
- **MCP server:** `src.codewalk.mcp.server:mcp`
- **Cloud worker:** `src.codewalk.worker.indexer:worker_loop`

## Connections to other folders

- `pipeline.py` imports from `ingestion/`, `embeddings/`, `analysis/`, `graph/`, `doc_knowledge/`, `review/`. It also writes `.codewalk/manifest.json` with the active ChromaDB `collection_name` so later loads use the right collection.
- `pipeline.build_full_analysis()` now takes a `collection_name` argument so doc/guideline collections stay aligned with the code collection.
- `pipeline.incremental_reindex()` now tolerates unreadable files (`read_file_content()` returning `None`) instead of crashing.
- `api/main.py` refreshes the `VectorStore` handle after indexing so the store kept in `api/state.py` always points at live Chroma collections.
- `api/state.py` repopulates an empty DuckDB `files` table during `load_scoped_analysis()` so existing Chroma indexes remain usable after schema migrations.
- `cli.py` calls `pipeline.*` and `team_config.*`.
- `config.py` is imported by almost every other package.
- `team_config.py` is used by `pipeline.py`, `api/state.py`, `api/cloud.py`, `worker/indexer.py`.
