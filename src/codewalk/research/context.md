# `src/codewalk/research/` — Deep Research

This package runs multi-angle research questions by fanning out sub-queries, reflecting on the results, and producing a synthesized markdown report.

## Modules

| File | Role |
|------|------|
| `deep_research.py` | LangGraph definition: decompose → parallel research → synthesize → reflect. Exported as `deep_research()`. |
| `planner.py` | `decompose()` — breaks a complex question into `SubQuestion` objects. |
| `researcher.py` | `make_researcher()` — runs retrieval for one sub-question and returns `SubFindings`. |
| `synthesizer.py` | `synthesize()`, `merge_findings()`, `reflect()` — builds the final markdown report and sources. |

## Data flow

```
research question
    ↓
planner.decompose() → list[SubQuestion]
    ↓
core/fanout.py runs a researcher node for each sub-question in parallel
  (each researcher uses rag/chain.py retrieve_corrective / VectorStore.search)
    ↓
synthesizer.merge_findings() + synthesize() → markdown report
    ↓
core/reflect.py critic pass
    ↓
final StructuredReport (markdown + sources + optional diagram)
```

## Connections

- Uses `rag/chain.py` / `embeddings/vector_store.py` / `graph/graph_store.py` for retrieval.
- Uses `core/fanout.py` and `core/reflect.py`.
- Exposed via API `/research`.
