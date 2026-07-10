# `src/codewalk/mcp/` — Model Context Protocol Server

This package exposes Codewalk as an MCP server that IDE agents (Cursor, Copilot, Claude Code) can call.

## Modules

| File | Role |
|------|------|
| `server.py` | FastMCP server with 43 tools: setup, query, architecture, review (including batched review: run_review, re_review, review_next_batch, submit_batch_findings, get_review_summary), docs, maintenance, voice, HITL, cloud index sync, config generation, version check, and knowledge-graph launch. |

## Data flow

```
MCP host calls codewalk_analyze_codebase
    ↓
repo root discovered via codewalk.yaml (auto-created if missing)
    ↓
state.set_repo_path(repo_root)
    ↓
{no index} → full_index_parallel
{complete index} → load_scoped_analysis + build_full_analysis
{partial index} → status="behind" warning; caller runs codewalk_incremental_reindex
    ↓
query/review/voice/docs/cloud tools read from state
```

## Connections

- Uses `api/state.py` as the single source of truth.
- Imports from `embeddings/`, `rag/`, `query/`, `review/`, `agent/`, `doc_knowledge/`, `pipeline/`, `codewalk_config/`.
- Cloud index tools (`codewalk_pull_index`, `codewalk_connect_repo`) talk to `CODEWALK_SERVER_URL`.
- HITL flow: `codewalk_approve_action()` sets `_pending_approval_token`; `codewalk_apply_fix()` requires the token.

## Running the MCP server

The server is usually launched by an IDE agent. Because the target repo's `cwd` may differ from the Codewalk source directory, invoke it with `CODEWALK_PATH` prepended to `sys.path`:

```bash
CODEWALK_PATH=/path/to/codewalk python -c \
  "import os, sys; sys.path.insert(0, os.environ['CODEWALK_PATH']); \
   from src.codewalk.mcp.server import mcp; mcp.run(transport='stdio')"
```

`cwd` should be the repo to analyze; `CODEWALK_PATH` should point to the cloned Codewalk repository.

See `MCP_EXAMPLES.md` in the Codewalk repo for example prompts per tool.

## Recent fixes

- `_reset_state()` now closes the active `GraphStore` (DuckDB) connection and clears the cached reference, so switching repos (A → B → A) reopens the correct `graph.duckdb` instead of using a stale handle.
- `codewalk_analyze_codebase(mode="auto")` no longer silently resumes partial indexes; it reports `status="behind"` and tells the host to run `codewalk_incremental_reindex`.
- `codewalk_search_codebase` performs a single deterministic search. The MCP instructions now include a "MULTI-ANGLE SEARCH" section that tells the host LLM to call it 1–3 times with different phrasings for every conceptual question and synthesize the returned chunks.
- Review sessions now write human-readable Markdown companions (`static_findings.md`, `llm_findings.md`) alongside the JSON source-of-truth files. The Markdown is hard-wrapped for easy reading; tools continue to read JSON.
- `codewalk_re_review` starts a fresh batched review linked to the previous session. Findings the user rejected in the previous session are recorded in `batch_state.json` and filtered out of the re-review summary. Submitted findings now receive stable IDs so they can be matched across review runs.
- MCP and API review flows both use `llm_findings.json` as the single source of truth for LLM findings.
