# `src/codewalk/review/` — Code Review Engine

This package performs LLM-based code review on diffs and single files, optionally guided by team docs/guidelines and rubrics, and applies approved fixes safely.

## Modules

| File / Package | Role |
|------|------|
| `engine.py` | Main orchestrator. `run_review()` runs the full multi-stage review; `run_review_context()` returns the context package used by MCP without running the LLM review pass. |
| `report.py` | Review data models: `Finding`, `ReviewReport`, `ReviewContextPackage`, plus enums for `Severity`, `Category`, `Verdict`, `Confidence`, `Source`, `Pillar`. |
| `finding_store.py` | Persistent finding store: `build_finding_store`, `save_finding_store`, `load_finding_store`, `diff_findings`, `find_last_review`. |
| `session.py` | `ReviewSession` dataclass and `SessionStatus` enum. |
| `session_store.py` | Save/load review sessions and findings to `.codewalk/review_session/<folder_name>/`. |
| `diff_parser.py` | `get_diff()` and `get_parsed_diff()` — git diff generation and `unidiff` parsing into `DiffFile`/`DiffHunk`/`ChangedLine`. |
| `fix_applier.py` | `apply_fix_to_file()` / `apply_fixes_batch()` — exact-replacement atomic writes with path-traversal guards, context-line disambiguation, and optional post-apply AST validation. |
| `neighborhood.py` | `expand_neighborhood()` — blast-radius and import-neighbor context expansion around changed files. |
| `static_analysis.py` | Deterministic static analysis (`run_static_analysis`) — graph-based risk scoring, PageRank, fan-in, cycle detection, bottleneck identification. |
| `stack_detect.py` | Detect the project's tech stack (languages, frameworks, architecture, state management, data layer, testing, API style) from the file tree, with LLM-first detection and deterministic fallback. Persists results to `.codewalk/stack_context.json`. |
| `utils.py` | Shared utilities: git HEAD SHA, token counting, import-block extraction, smart file truncation around hunks. |
| `metrics.py` | `compute_metrics()` — aggregate statistics from a review run. |
| `verdict.py` | Verdict computation helpers. |
| `rubric_loader.py` | `build_rubrics()` — loads YAML rubric definitions into `Rubrics`. |
| `cancellation.py` | Per-session cancellation tokens: `start_review`, `check_cancelled`, `end_review`, `ReviewCancelledError`. |
| `progress.py` | Progress callbacks and reporting helpers. |
| `adversarial.py` | Adversarial verification of LLM findings (skeptical re-review). |
| `eval.py` | Evaluation helpers for comparing review output against expected issues. |
| `review_cache.py` | Deterministic review-input cache keyed by repo HEAD + `codewalk.yaml` mtime + diff target. |
| `reviewers/` | Pluggable reviewer implementations: `BaseReviewer`, `GenericReviewer`, `SecurityReviewer`, `ReviewerRegistry`, `DEFAULT_REVIEWERS`. |
| `pipeline/` | Post-processing pipeline: `cluster`, `deduplicate`, `rank`, `verify`, `write_summary`/`write_narrative_summary`, `compute_verdict`. |
| `renderers/` | Output formatters for different consumers: `markdown`, `cli`, `api`, `base`. |

## Data flow

```
git diff or single file content
    ↓
diff_parser.py → DiffFile list
    ↓
neighborhood.py → expanded context + blast radius
    ↓
static_analysis.py → deterministic auto-findings (+ review_cache lookup/save)
    ↓
engine.py assembles ReviewInputs (guidelines, architecture flags, file tree, rubrics)
    ↓
reviewers/ (GenericReviewer, SecurityReviewer, …) run in token-bounded batches
    ↓
pipeline/ → cluster → deduplicate → rank → verify (adversarial) → compute_verdict → write summary
    ↓
finding_store.py persists completed reviews under `.codewalk/reviews/`; session_store.py persists active/review sessions under `.codewalk/review_session/<folder_name>/`
    ↓
renderers/ format output for CLI / API / Markdown
```

## Connections

- `engine.py` imports from every other module in this package and from `src.codewalk.config.get_llm()` for the review LLM.
- `engine.py` uses `src.codewalk.codewalk_config.load_codewalk_yaml()` to discover repo configuration and guidelines.
- `neighborhood.py` uses `src.codewalk.analysis.blast_radius` and `src.codewalk.graph.graph_runtime` for graph-based context expansion.
- `static_analysis.py` uses `src.codewalk.graph.graph_runtime` for PageRank/fan-in/cycle data and `src.codewalk.analysis.dependency_graph` for import parsing.
- API endpoints in `src/codewalk/api/main.py` call into `engine.run_review()`, `engine.run_review_context()`, and `fix_applier.apply_fix_to_file()`.
- MCP tools in `src/codewalk/mcp/server.py` expose `codewalk_run_review` (context), `codewalk_review_file` (full pipeline), `codewalk_get_review_details`, `codewalk_get_stack_info`, `codewalk_finding_verdict`, `codewalk_apply_accepted`, `codewalk_approve_action`, `codewalk_apply_fix`, and `codewalk_verify_fix`.

## Notes

- The older monolithic `reviewer.py` has been split into `engine.py` + `reviewers/` + `pipeline/` + `renderers/`.
- `run_review()` is the single entry point used by both the API `/review` endpoint and the MCP `codewalk_run_review` tool.
