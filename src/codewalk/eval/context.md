# `src/codewalk/eval/` — Evaluation & Benchmarking

This package evaluates RAG quality using the RAGAS framework and synthetic QA generation.

## Modules

| File | Role |
|------|------|
| `evaluator.py` | RAGAS-based evaluation harness over a dataset of (question, context, answer, ground_truth). |
| `qa_generator.py` | Generates synthetic question/answer pairs from indexed chunks for benchmarking. |

## Connections

- Not part of the runtime API/MCP/CLI flow.
- Consumes `embeddings/vector_store.py` and `rag/chain.py` for retrieval and answer generation.
- Used in development to measure retrieval/answer quality.

## Known issues

- RAGAS import path deprecation warnings for metrics (cosmetic, will break in RAGAS v1.0).
