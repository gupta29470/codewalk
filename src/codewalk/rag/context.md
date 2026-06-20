# `src/codewalk/rag/` — Corrective Retrieval-Augmented Generation

This package retrieves code chunks for a natural-language query, expands weak retrieval via the dependency graph, grades results, and formats context for LLM prompts.

## Modules

| File | Role |
|------|------|
| `chain.py` | `retrieve_corrective()` — main entry point: symbol lookup → vector search → distance filter → query expansion / graph expansion → keyword grading. `ask_corrective()` for LLM answer generation with retries. `ask()` for simple one-shot RAG. |
| `symbol_lookup.py` | Deterministic symbol lookup against DuckDB; fetches exact symbol chunks + callers/callees. |
| `retrieval_quality.py` | `filter_by_distance()` and `is_retreival_good()` — cosine-distance thresholds and retrieval-confidence gate. |
| `graph_expansion.py` | `expand_via_graph()` — uses DuckDB import/importer edges to add neighbor-file chunks when retrieval is weak. |
| `query_expander.py` | LLM-based query expansion into multiple retrieval queries + symbol hint. |
| `query_rewriter.py` | LLM rewrites a failed query for corrective-RAG retries. |
| `query_router.py` | Routes a question to `direct` (exact symbol), `search`, `module`, or `overview`. |
| `chunk_grader.py` | `grade_chunks_free()` (keyword overlap, no LLM) and `grade_chunks()` (batched LLM relevance grader). |
| `reranker.py` | LLM-as-reranker scoring chunks 0–10. |
| `answer_grader.py` | `grade_answer()` — scores generated answers for faithfulness and relevance (used only in `ask_corrective`). |
| `prompts.py` | System and question prompts for the RAG answer chain. |

## Data flow (deterministic / MCP path)

```
query string
    ↓
lookup_symbol() → exact-symbol chunks (distance 0)
VectorStore.search() → semantic-search child chunks
    ↓
filter_by_distance() → drop low-similarity chunks
    ↓
expand_via_graph() → add neighbor-file chunks if retrieval is weak
    ↓
grade_chunks_free() → keyword relevance grading
    ↓
format_context() → text block for the LLM/host
```

## Data flow (corrective RAG path, `ask_corrective`)

Same as above, but when retrieval is weak it also uses `expand_query()` and `rewrite_query()`, then `grade_chunks()` (LLM chunk grader), generates an answer, and finally `grade_answer()` (LLM answer grader). Up to 5 retries if grades are poor.

## Connections

- Consumes `embeddings/vector_store.py` and `graph/graph_store.py` / `graph_runtime.py`.
- Used by `api/main.py` chat endpoints, `mcp/server.py` `codewalk_search_codebase`, `review/reviewer.py`, and `research/`.
- `chunk_grader.py` uses `sklearn` stop-words for the free keyword grader.

## Recent fixes

- `chain.py` now guards query expansion so a single-query (or malformed) expansion no longer raises `IndexError`.

## Known issues

- `chain.py` imports `is_retreival_good` (typo in name) but that matches the function definition; purely cosmetic.
