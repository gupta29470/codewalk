# `src/codewalk/review/` — Code Review Engine

This package performs LLM-based code review on diffs and single files, optionally guided by team docs/guidelines and rubrics, and applies approved fixes safely.

## Modules

| File / Package | Role |
|------|------|
| `engine.py` | Main orchestrator. `run_review()` runs the batched LLM review and returns raw findings; `run_review_context()` returns the context package used by MCP without running the LLM review pass. Exposes `build_graph_only()` for on-the-fly graph builds when no ChromaDB index exists. |
| `report.py` | Review data models: `Finding`, `ReviewReport`, `ReviewContextPackage`, `ArchitectureFlags`, plus enums for `Severity`, `Category`, `Confidence`, `Source`, `Pillar`. |
| `session.py` | `ReviewSession` dataclass and `SessionStatus` enum. |
| `session_store.py` | Save/load review sessions and findings to `.codewalk/review_session/<folder_name>/`. |
| `diff_parser.py` | `get_diff()` and `get_parsed_diff()` — git diff generation (staged + unstaged + untracked by default, three-dot branch diff for target_branch) and `unidiff` parsing into `DiffFile`/`DiffHunk`/`ChangedLine`. Includes `_synthetic_untracked_diff()` for new files not yet staged. |
| `editor.py` | `apply_edit()` — unified editor (exact replacement + LLM-as-editor fallback, language-aware syntax validation via ast/tree-sitter, retries incl. empty responses). `dry_run=True` returns original/modified content without writing (API preview flow). `write_approved_edit()` writes user-approved diffs with backup + validation. `verify_and_rollback()` runs static analysis + tests and rolls back failures. Used by agent `apply_fix` and API `/review/preview-edits` + `/review/apply-edits`. (MCP never edits files; `codewalk_accept_and_verify_fix` only returns accepted findings.) |
| `neighborhood.py` | `expand_neighborhood()` — blast-radius and import-neighbor context expansion around changed files. |
| `static_analysis.py` | Deterministic static analysis (`run_static_analysis`) — graph-based risk scoring, PageRank, fan-in, cycle detection, bottleneck identification. |
| `stack_detect.py` | Detect the project's tech stack (languages, frameworks, architecture, state management, data layer, testing, API style) from the file tree, with LLM-first detection and deterministic fallback. Persists results to `.codewalk/stack_context.json`. |
| `utils.py` | Shared utilities: git HEAD SHA, token counting, import-block extraction, smart file truncation around hunks. |
| `rubric_loader.py` | `build_rubrics()` — loads YAML rubric definitions into `Rubrics`. |
| `context_builder.py` | `build_unified_batch_context()` — shared review prompt for API and MCP. MCP sets `include_host_instructions=True` to prepend `REVIEW_INSTRUCTIONS`; API uses `_UNIFIED_REVIEW_SYSTEM_PROMPT` as the LLM system message instead. Also exports `estimate_shared_context_tokens()`. |
| `cancellation.py` | Per-session cancellation tokens: `start_review`, `check_cancelled`, `end_review`, `ReviewCancelledError`. |
| `progress.py` | Progress callbacks and reporting helpers. |
| `eval.py` | Evaluation helpers for comparing review output against expected issues. |
| `review_cache.py` | Deterministic review-input cache keyed by repo HEAD + `codewalk.yaml` mtime + diff target. |
| `reviewers/` | Shared review contract (`BaseReviewer`, `ReviewContext`) and `run_structured_review()` — a single structured-output LLM call with raw-JSON fallback. |
| `renderers/` | Output formatters: `markdown`, `api`, `base`. The legacy CLI renderer was removed. |

## Data flow

```
git diff or single file content
    ↓
diff_parser.py → DiffFile list
    ↓
engine._load_graph_runtime() → loads or builds dependency graph on-the-fly
    (if no .codewalk/graph.duckdb: scan → deps → modules → DuckDB ~5s, cached after)
    ↓
neighborhood.py → expanded context + blast radius
    ↓
static_analysis.py → deterministic auto-findings (+ review_cache lookup/save)
    ↓
engine.py assembles ReviewInputs (guidelines, architecture flags, file tree, rubrics)
    ↓
engine.py creates hybrid batches (semantic source+test grouping, then token-budget split) and context_builder.py builds one unified prompt per batch
    ↓
API: reviewers.run_structured_review() with `_UNIFIED_REVIEW_SYSTEM_PROMPT`
MCP: returns the same batch context (plus host REVIEW_INSTRUCTIONS) to the IDE agent
    ↓
session_store.py persists active/review sessions under .codewalk/review_session/<folder_name>/.
    Session folders contain `session.json`, `static_findings.json`, `llm_findings.json`, and Markdown companions
    (`static_findings.md`, `llm_findings.md`) for human reading.
    ↓
renderers/ format output for API JSON / Markdown
```

> Note: The legacy post-processing step (cluster → deduplicate → rank → verify → compute_verdict → write summary) has been removed from the codebase. The API returns raw findings and the MCP path returns context for the host LLM.

## Connections

- `engine.py` imports from every other module in this package and from `src.codewalk.config.get_llm()` for the review LLM.
- `engine.py` uses `src.codewalk.codewalk_config.load_codewalk_yaml()` to discover repo configuration and guidelines.
- `neighborhood.py` uses `src.codewalk.analysis.blast_radius` and `src.codewalk.graph.graph_runtime` for graph-based context expansion.
- `static_analysis.py` uses `src.codewalk.graph.graph_runtime` for PageRank/fan-in/cycle data and `src.codewalk.analysis.dependency_graph` for import parsing.
- API endpoints in `src/codewalk/api/main.py` call into `engine.run_review()`, `engine.run_review_context()`, `editor.apply_edit(dry_run=True)` (preview-edits), and `editor.write_approved_edit()` + `editor.verify_and_rollback()` (apply-edits).
- MCP tools in `src/codewalk/mcp/server.py` expose `codewalk_run_review` (context), `codewalk_re_review` (re-review with previous findings context), `codewalk_review_file` (full pipeline), `codewalk_get_review_details`, `codewalk_get_stack_info`, and `codewalk_accept_and_verify_fix` (read-only — returns accepted findings for the host to apply itself).

## Notes

- The older monolithic `reviewer.py` has been split into `engine.py` + `context_builder.py` + `reviewers/` + `renderers/`.
- `run_review()` is the single entry point used by the API `/review` endpoint and the MCP `codewalk_review_file` tool. Re-reviews load previous findings via `session_store.py` and pass them as `previous_findings` to `run_review()`.
- Both API and MCP paths share `context_builder.build_unified_batch_context()`. MCP prepends `REVIEW_INSTRUCTIONS` and injects `codewalk.yaml` guidelines; API keeps instructions in the system prompt and also injects guidelines.
- MCP `group_files_for_review()` uses hybrid batching: semantic groups (max 5 files, source+test pairing, risk-sorted) then splits oversized groups to a **200k** token budget (aligned with the API path) that accounts for shared instructions/stack/rubrics/guidelines. Per-file content cap is 40k; modified-file diffs are capped at 20k with all removals retained. New files skip the diff block to avoid duplicating content.
- Both paths size batches with the same estimator, `engine.estimate_file_prompt_tokens()`: the API's `_estimate_file_tokens()` is a caching wrapper around it. Keeping one function means API grouping reflects the caps `build_unified_batch_context()` actually applies instead of over-splitting on full hunk tokens.
- MCP `codewalk_submit_batch_findings` validates required fields including `category`, rejects out-of-batch `file_path`, and rejects wildly out-of-range `line_number`. Empty batches still require `notes`.
- Team guidelines: set `code_guidelines` in `codewalk.yaml` to an explicit file path, or place `code_guidelines.md` (or `.txt`/`.rst`) inside `docs_path`; it is loaded automatically by `review/utils.py.load_code_guidelines_text()`.
- Rubrics: team overrides go in `.codewalk/rubrics/<name>.md` (e.g. `core.md`, `python.md`, `python_fastapi.md`, `typescript_nextjs.md`). Built-in rubrics live in `src/codewalk/review/rubrics/`.
- API review responses contain `issues` (LLM findings), `static_issues` (deterministic/static findings), `files_reviewed`, `lines_added`, `lines_removed`, `session_id`, and `architecture_flags`. There is no server-side `verdict`, `summary`, `clusters`, or `merge_blockers`.
