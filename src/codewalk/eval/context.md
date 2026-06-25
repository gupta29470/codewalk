# `src/codewalk/eval/` — Evaluation & Benchmarking

This package evaluates retrieval, answer quality, and review quality. It is not part of the runtime API/MCP/CLI flow; it is used in development and benchmarking.

## Modules

| File | Role |
|------|------|
| `dataset.py` | `EvalSample` dataclass and helpers to load/convert evaluation datasets to RAGAS format. |
| `evaluator.py` | RAGAS-based evaluation harness over a dataset of (question, context, answer, ground_truth); also computes internal metrics. |
| `experiments.py` | `run_experiment()`, `sweep_experiment()` — A/B toggles for retrieval features (graph expansion, chunk grader) and result comparison. |
| `generate_multilang_review_fixtures.py` | Generates synthetic multi-language review test fixtures for evaluating the review engine. |
| `metrics.py` | Save/load/compare evaluation runs and trend analysis. |

## Connections

- Not part of the runtime API/MCP/CLI flow.
- Consumes `embeddings/vector_store.py` and `rag/chain.py` for retrieval and answer generation.
- Consumes `review/engine.py` and `review/report.py` for review fixture evaluation.
- Used in development to measure retrieval/answer quality and review performance.

## Known issues

- RAGAS import path deprecation warnings for metrics (cosmetic, will break in RAGAS v1.0).
