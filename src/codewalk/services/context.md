# `src/codewalk/services/` — Deterministic Service Layer

This package exposes thin, deterministic wrappers around retrieval logic so that API endpoints and MCP tools share the same code paths without duplicating orchestration. No LLM calls happen here.

## Modules

| File | Role |
|------|------|
| `search_service.py` | `search()` — deterministic retrieval pipeline: symbol lookup → semantic search → distance filter → keyword grading → optional graph expansion. |
| `symbol_service.py` | `lookup()` — thin wrapper around `rag.symbol_lookup` for symbol-centric chunk retrieval. |

## Data flow

```
API / MCP request
    ↓
search_service.search(query, store, graph_store)
    ↓
lookup_symbol → VectorStore.search → filter_by_distance → grade_chunks_free → expand_via_graph
    ↓
list[chunk], confidence, retrieval_good
```

## Connections

- `search_service.py` consumes `rag.symbol_lookup`, `rag.retrieval_quality`, `rag.chunk_grader`, and `rag.graph_expansion`.
- `symbol_service.py` delegates to `rag.symbol_lookup`.
- Used by `mcp/server.py` and `api/main.py` endpoints that need raw retrieval without LLM generation.

## Notes

- No LLM calls happen in this package; it is purely retrieval and context assembly.
- Review-context gathering is handled directly by `review/engine.py` (`run_review_context`), not by this services package.
