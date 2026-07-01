# `src/codewalk/research/` — Deep Research

This package runs multi-angle research questions by fanning out sub-queries, reflecting on the results, and producing a synthesized markdown report.

## Modules

| File | Role |
|------|------|
| `deep_research.py` | LangGraph definition: decompose → parallel research → synthesize → reflect. Exported as `deep_research()`. |
| `planner.py` | `decompose()` — breaks a complex question into `SubQuestion` objects. |
| `researcher.py` | `make_researcher()` — runs retrieval for one sub-question and returns `SubFindings`. |
| `synthesizer.py` | `make_synthesizer(graph_store)` / `synthesize()`, `merge_findings()`, `reflect()` — builds the final markdown report and sources. |
| `diagram_generator.py` | `generate_research_diagram()` / `generate_research_graph_context()` — builds grounded React Flow-style nodes/edges from DuckDB for the research report. |

## Data flow

```
research question
    ↓
planner.decompose() → list[SubQuestion]
    ↓
core/fanout.py (via `build_fanout_graph`) runs a researcher node for each sub-question in parallel
  (each researcher uses rag/chain.py retrieve_corrective / VectorStore.search)
    ↓
synthesizer.merge_findings() + `make_synthesizer(graph_store)` → markdown report
    ↓
core/reflect.py critic pass
    ↓
final StructuredReport (markdown + sources + optional structured diagram)
```

## Connections

- Uses `rag/chain.py` / `embeddings/vector_store.py` / `graph/graph_store.py` for retrieval and graph-grounded synthesis.
- Uses `core/fanout.py` (compiled with `compile_with_hitl(..., async_checkpointer=True)`) and `core/reflect.py`.
- `deep_research()` internally runs an async pipeline (`_run_research_pipeline`) using `async with graph_ctx`.
- Exposed via API `/research`.
