# `src/codewalk/review/` — Code Review & Fix Application

This package performs LLM-based code review on diffs and single files, optionally guided by team docs/guidelines, and applies approved fixes safely.

## Modules

| File | Role |
|------|------|
| `reviewer.py` | Main review orchestrator: diff/file review, architecture context, blast radius, guidelines/docs search, parallel LLM passes. |
| `diff_parser.py` | `get_diff()` and `get_parsed_diff()` — git diff generation and `unidiff` parsing. |
| `guidelines_loader.py` | `get_guidelines_store()` — loads `.md/.txt/.rst` guidelines into a ChromaDB collection. |
| `fix_applier.py` | `apply_fix_to_file()` / `apply_fixes_batch()` — exact-replacement atomic writes with path-traversal guards. |
| `reflector.py` | Critic/reflection prompt for a second-pass review. |
| `models.py` | Pydantic models for diff hunks, changed lines, review issues. |

## Data flow

```
git diff or file content
    ↓
retrieve_corrective() + caller/blast context + guidelines/docs context
    ↓
LLM review → list[Issue]
    ↓
(optional) reflector.py second pass
    ↓
approved fixes → fix_applier.py
```

## Connections

- `reviewer.py` uses `rag/chain.py`, `query/`, `embeddings/vector_store.py`, `graph/graph_store.py`, and `doc_knowledge/doc_store.py`.
- `review_context_service.py` wraps `reviewer.prepare_review_context()` for API/MCP review-context endpoints.
- API endpoints: `/review`, `/review/file`, `/review/apply`, `/review/guidelines`.
- MCP tools: `codewalk_get_review_context`, `codewalk_reflect_review`, `codewalk_apply_fix`, `codewalk_verify_fix` call into this package.

## Recent fixes

- `review_prompts.py` now instructs the LLM to classify issues as BLOCKING vs NON-BLOCKING and to keep the verdict consistent (Request changes when blocking issues exist).
- `review_prompts.py` adds explicit guidance for reviewing sensitive domains (payments, auth, crypto, PII): validation, token/session handling, error paths, backward compatibility, and exhaustiveness of enums/region maps.
- `review_prompts.py` asks the model to separate praise from issues and to avoid letting nits dilute blocking problems.
- `reviewer.py` handles empty files gracefully and reviews them without generating an invalid diff.
- `reviewer.py` now accepts an explicit `guidelines_path` for single-file reviews.
- `reviewer.py` imports `log as _log` from `src.codewalk.log` so single-file review no longer raises `NameError`.
- `fix_applier.py` now applies whitespace-normalized fixes by matching the original file span, instead of silently failing when spaces/tabs differ.
- `fix_applier.py` context-line disambiguation now replaces only `old_code`, preserving the surrounding context lines.
- `fix_applier.py` guards against path traversal when applying fixes.
