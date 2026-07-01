# `src/codewalk/graph/` — Graph Storage & Runtime

This package persists codebase structure in DuckDB and provides fast in-memory graph traversal via `igraph`.

## Modules

| File | Role |
|------|------|
| `graph_store.py` | `GraphStore` — DuckDB schema (files, imports, symbols, symbol metadata, class hierarchy, class members, symbol calls, modules, chunks). Populated by `populate_from_analysis()`. |
| `graph_runtime.py` | `GraphRuntime` — loads DuckDB into an `igraph` graph for centrality, cycles, shortest paths, and module-to-module flow. |
| `knowledge_graph_export.py` | Exports DuckDB symbols/imports to `knowledge-graph.json` for interactive visualisation. |

## Schema overview (10 tables)

1. `files` — one row per scanned file.
2. `imports` — directed file-to-file import edges.
3. `symbols` — functions/classes/methods extracted during analysis.
4. `symbol_metadata` — decorators, route info, entry-point flags.
5. `class_hierarchy` — class → parent-class inheritance edges.
6. `class_members` — class → method/function member edges.
7. `symbol_calls` — call edges between symbols.
8. `chunks` — links ChromaDB chunk indices to symbols/files.
9. `modules` — auto-detected module groupings.
10. `module_deps` — inter-module dependency edges.

## Data flow

```
analysis/dependency_graph.py + analysis/module_detector.py
    ↓
GraphStore.populate_from_analysis(files, deps, modules_result, embedded_chunks)
    ↓
GraphRuntime(graph_store) → centrality, cycles, call chains
    ↓
query/, generation/, research/, rag/, review/
```

## Connections

- `pipeline.build_full_analysis()` creates the DuckDB file and triggers knowledge-graph export.
- `api/state.py` opens a `GraphStore` + `GraphRuntime` after analysis and stores them in module state.
- `query/`, `rag/chain.py`, `review/`, `generation/`, `research/diagram_generator.py`, and `mcp/server.py` read from the graph runtime/store.

## Recent fixes

- DuckDB `chunks.embedding_id` values now match ChromaDB chunk IDs (`file_path::chunk_type::chunk_index`).
- DuckDB `chunks.chunk_id` now includes `chunk_type` so parent and child chunks with the same index no longer collide.

## Notes

- Incremental reindex updates Chroma incrementally, then fully rebuilds DuckDB and `knowledge-graph.json` from all Chroma chunks so the `chunks` table stays consistent.
