"""Codewalk MCP server — 41 tools for codebase onboarding, search, review,
docs, voice, visualization, and cloud index management.

Tool categories:
  SETUP:         analyze_codebase, generate_config
  QUERY:         search_codebase, get_module_info, explain_function, explain_class,
                 lookup_symbol, get_overview, get_blast_radius_map,
                 find_circular_dependencies, get_reading_order, get_execution_flow
  ARCHITECTURE:  get_architecture_health, call_chain
  REVIEW:        run_review, re_review, review_next_batch, submit_batch_findings,
                 get_review_summary, review_file, get_review_details,
                 get_stack_info, save_stack_context,
                 apply_and_verify_fix
  MAINTENANCE:   incremental_reindex, refresh_analysis,
                 load_guidelines, run_static_analysis, run_tests
  VOICE:         voice_ask, speak
  DOCS:          index_docs, search_docs, ask_docs
  HITL:          approve_action, apply_fix
  CLOUD:         pull_index, index_status, connect_repo, check_version
  VISUALIZATION: show_knowledge_graph

Review architecture (batched, no external LLM calls):
  The MCP review path NEVER calls an LLM. It returns raw context to the host LLM.
  Files are grouped into batches of 3-5, the host reviews each batch, submits
  findings to disk via submit_batch_findings, then moves to the next batch.
  After all batches, get_review_summary reads the persisted JSONs and returns
  a structured summary for the host to produce the final verdict.

  Each codewalk_run_review call always creates a fresh session — there is no
  automatic session reuse. Old sessions remain on disk and can be queried
  via get_review_summary and apply_and_verify_fix.

  Diff coverage: by default, get_diff() returns ALL changes (staged + unstaged
  + untracked files). The only narrow mode is staged=True. target_branch uses
  two-dot diff (committed + staged + unstaged + untracked vs branch tip).

  Graph-on-the-fly: if no .codewalk/graph.duckdb exists when a review starts,
  _load_graph_runtime() automatically builds the dependency graph (~3-7s) and
  persists it to .codewalk/graph.duckdb. This gives the review full architecture
  awareness (blast radius, PageRank, cycles, bottlenecks) without requiring
  codewalk_analyze_codebase. No ChromaDB, no embeddings, no model download.
  Subsequent reviews load the cached graph in ~100ms.

  Stack detection flow (MCP path — NO LLM calls):
    1. Any tool that needs stack checks .codewalk/stack_context.json
    2. If file exists → use it (persists across ALL commits)
    3. If file missing → return "Stack Context Required" with instructions
    4. Host calls codewalk_get_stack_info → gets file tree + prompt
    5. Host fills JSON, calls codewalk_save_stack_context → writes file
    6. Host re-calls the original tool → reads file → proceed
    7. To refresh: refresh_stack=True or call get_stack_info + save again

  Tools that require stack context:
    codewalk_run_review, codewalk_review_file,
    codewalk_get_overview, codewalk_get_architecture_health

  codewalk_analyze_codebase prompts for stack setup after indexing completes
  (status ready/built/reindexed). If the index is behind, it asks for reindex
  first and defers the stack-context prompt until the index is synced.

  IMPORTANT: No MCP review tool calls detect_stack() or any external LLM.
  Stack context comes purely from the persistent file on disk.
  For complex questions, the host LLM can call codewalk_search_codebase
  1-3 times with different phrasings and synthesize the returned chunks.

  Session directory layout:
    .codewalk/review_session/<session>/
    ├── session.json          # session metadata
    ├── batch_state.json      # batch queue, rubrics, index pointer
    ├── static_findings.json  # deterministic findings (written once at start)
    ├── static_findings.md    # human-readable companion to static_findings.json
    ├── llm_findings.json     # host LLM findings (appended per batch)
    └── llm_findings.md       # human-readable companion to llm_findings.json

  JSON files are the source of truth for tools. Markdown files are read-only
  and hard-wrapped for easy reading in any editor.
"""

import inspect
import logging
import os
import sys
import json
import secrets
import shutil
import subprocess
from pathlib import Path
import requests

# Allow the MCP host to run the server from the target repo while keeping the
# Codewalk source package elsewhere. If CODEWALK_PATH is set, add it to the
# import path so `src.codewalk.*` resolves.
_codewalk_path = os.environ.get("CODEWALK_PATH")
if _codewalk_path:
    _codewalk_path = os.path.abspath(_codewalk_path)
    if _codewalk_path not in sys.path:
        sys.path.insert(0, _codewalk_path)

from mcp.server.fastmcp import FastMCP

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

# Single-use HITL token from last codewalk_approve_action (host UI approves; code enforces token)
_pending_approval_token: str | None = None

from src.codewalk.rag.chain import format_context
from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT
from src.codewalk.services.search_service import search as deterministic_search
from src.codewalk.services.symbol_service import lookup as deterministic_symbol_lookup


from src.codewalk.codewalk_config import load_codewalk_yaml
from src.codewalk.ingestion.config_generator import generate_codewalk_yaml
from src.codewalk.config import settings
from src.codewalk.voice.stt import record_audio, transcribe
from src.codewalk.voice.tts import speak, stop_speaking
from src.codewalk.api import state
from src.codewalk.query import (
    resolve_module_name, module_not_found_error, short_name,
    resolve_module_with_fallback, compute_file_risks,
    explain_function_text, overview_text, blast_radius_map_text,
    reading_order_text, execution_flow_text,
)
from src.codewalk.doc_knowledge.prompts import DOC_ASK_PROMPT
from src.codewalk.review.report import ReviewContextPackage
from src.codewalk.review.session_store import load_session


# ─── Create the MCP server ──────────────────────────────────────────
mcp = FastMCP(
    name="codewalk",
    instructions=(
        "Codewalk is an AI-powered codebase onboarding tool. "
        "\n\n"
        "## IMPORTANT: ALWAYS USE CODEWALK TOOLS FIRST\n"
        "When a Codewalk tool exists for the task, you MUST call it first instead of\n"
        "searching, reading, or analyzing files yourself. Codewalk tools use\n"
        "pre-computed dependency graphs, ChromaDB embeddings, and blast radius\n"
        "analysis that you cannot replicate by reading files.\n"
        "\n"
        "ONLY fall back to reading/searching files manually if a Codewalk tool has\n"
        "already been tried and genuinely cannot answer the question (e.g., the file\n"
        "is not indexed, the query is about a non-code asset, or every relevant tool\n"
        "returned an empty/error result). Tools are the first priority.\n"
        "\n"
        "## SETUP WORKFLOW\n"
        "1) codewalk_generate_config() (optional) — create a starter codewalk.yaml with\n"
        "   stack-specific exclusions before analyzing.\n"
        "2) codewalk_analyze_codebase() — ONE CALL, no arguments:\n"
        "   • Uses the current MCP workspace directory (cwd) as the repo root.\n"
        "   • Local index is checked FIRST:\n"
        "     - .codewalk/ complete → INDEX READY (load only, no re-embed)\n"
        "     - .codewalk/ partial/chroma missing files → BEHIND warning; call codewalk_incremental_reindex to sync\n"
        "     - No local index + cloud configured → auto-download from server\n"
        "     - No local index + local only → scan (codewalk.yaml excludes), embed on this machine\n"
        "3) Query tools auto-load .codewalk/ on later MCP sessions (no re-analyze needed).\n"
        "\n"
        "## ANSWERING QUESTIONS (after setup)\n"
        "- 'What does function X do?' → codewalk_explain_function(X) — line-by-line explanation\n"
        "- 'Explain class X / component X' → codewalk_explain_class(X) — line-by-line explanation\n"
        "- 'How does feature Y work?' → codewalk_search_codebase(Y) — returns code chunks for YOU to analyze\n"
        "  Tip: for broad questions, call it 1-3 times with different phrasings and synthesize the chunks.\n"
        "- 'Give me an overview' → codewalk_get_overview\n"
        "- 'What's in module Z?' → codewalk_get_module_info(Z) — files + functions/classes\n"
        "- 'What breaks if I change X?' → codewalk_get_blast_radius_map(target=X) — "
        "X can be a module name, file name, or empty for top 30 riskiest\n"
        "- 'Are there circular dependencies?' → codewalk_find_circular_dependencies — "
        "returns cycle groups and suggested edges to break\n"
        "- 'Where should I start reading?' → codewalk_get_reading_order — returns ALL files "
        "(optionally pass module_name to scope to one module)\n"
        "- 'Show me the dependency flow' → codewalk_get_execution_flow — "
        "no arg = module-to-module flow, with module_name = file-to-file flow\n"
        "- 'Show me the knowledge graph' → codewalk_show_knowledge_graph — kills any "
        "running Codewalk frontend, auto-builds the production bundle if .next/ is missing, "
        "starts npm start on the requested port, and opens an interactive visualization "
        "in the browser (optional repo_path and port arguments)\n"
        "- 'Fix this bug / apply this change' → search + produce fix → codewalk_approve_action FIRST → then apply\n"
        "\n"
        "## MULTI-ANGLE SEARCH (use for question)\n"
        "For any question, run 1-3 parallel calls to codewalk_search_codebase\n"
        "with different phrasings, then synthesize the returned chunks into one answer.\n"
        "Example for 'how does auth work?':\n"
        "  1. codewalk_search_codebase('how does auth work')\n"
        "  2. codewalk_search_codebase('authentication login flow')\n"
        "  3. codewalk_search_codebase('verify user credentials')\n"
        "Merge the chunks, remove duplicates, and answer from the combined context.\n"
        "\n"
        "## ANSWER QUALITY RULES\n"
        "- When quoting code with obvious typos or odd identifiers, explicitly flag them so the user knows they are real source issues.\n"
        "- When reporting counts from grep/search, present them as approximate unless you verified them; reconcile counts before publishing.\n"
        "\n"
        "## MAINTENANCE (after code changes or interrupted indexing)\n"
        "- codewalk_incremental_reindex — re-embed changed files, resume partial indexes, and remove chunks for deleted files (hash-based)\n"
        "- codewalk_refresh_analysis — rebuild deps/modules without re-embedding\n"
        "\n"
        "## CODE REVIEW — FULL FLOW (agent-driven via MCP)\n"
        "\n"
        "Codewalk review tools are called by YOU (the IDE agent) over MCP — not by the user directly.\n"
        "The review uses a batched approach: files are grouped into small batches so you can\n"
        "review each thoroughly without context overflow. Findings are persisted to disk\n"
        "between batches so your context window stays clean. A human-readable Markdown\n"
        "companion (llm_findings.md / static_findings.md) is generated alongside the JSON\n"
        "files for easy reading in any editor; tools continue to use the JSON files.\n"
        "\n"
        "Step 0: STACK CONTEXT (one-time setup per repo).\n"
        "        Several tools (run_review, review_file, get_overview,\n"
        "        get_architecture_health) require `.codewalk/stack_context.json`.\n"
        "        If ANY tool returns 'Stack Context Required', follow the steps:\n"
        "          1. Call `codewalk_get_stack_info()` — returns file tree + prompt\n"
        "          2. Analyze it and produce the JSON describing the stack\n"
        "          3. Call `codewalk_save_stack_context(your_json)` to persist it\n"
        "          4. Re-call the tool that blocked — it will now proceed\n"
        "        This happens ONCE per repo — the file persists across all commits.\n"
        "        To refresh: `codewalk_run_review(refresh_stack=True)` or call\n"
        "        `codewalk_get_stack_info()` + `codewalk_save_stack_context()` again.\n"
        "        NOTE: codewalk_analyze_codebase also prompts you to do this after\n"
        "        indexing completes successfully — so if you follow that prompt, you're already set.\n"
        "\n"
        "Step 1: START — call codewalk_run_review() once.\n"
        "        No setup required — the dependency graph is built automatically on first\n"
        "        review (~5s) and cached for subsequent calls.\n"
        "        By default, reviews ALL changes: staged, unstaged, AND new untracked files.\n"
        "        Returns: session_id + first batch of 3-5 files with full context.\n"
        "        Layer 0 (deterministic) findings are saved to disk automatically.\n"
        "\n"
        "Step 2: REVIEW LOOP — for each batch:\n"
        "   a) Review the files: identify bugs, security issues, logic errors, style.\n"
        "   b) Call codewalk_submit_batch_findings(session_id, findings=[...]) to save.\n"
        "      Each finding: {file_path, line_number, severity, title, explanation,\n"
        "                      current_code, recommended_code, blocking}\n"
        "   c) Call codewalk_review_next_batch(session_id) to get next batch.\n"
        "   Repeat until 'All batches reviewed'.\n"
        "\n"
        "Step 3: SUMMARIZE — call codewalk_get_review_summary(session_id).\n"
        "        Returns: all Layer 0 warnings + all your LLM findings across batches.\n"
        "        Use this to produce the final verdict (approve / request_changes).\n"
        "\n"
        "Step 4: USER VERDICTS \u2014 user edits llm_findings.json directly,\n"
        "        setting user_verdict to 'accepted' or 'rejected' for each finding.\n"
        "        The file is initialized with user_verdict: null for every finding.\n"
        "\n"
        "Step 5 (optional): RE-REVIEW — codewalk_re_review().\n"
        "        Starts a fresh review (staged + unstaged + untracked) and hides any finding the user rejected in the\n"
        "        previous session. Pass target_branch='...' only when diffing against a branch. Use this after\n"
        "        the user has addressed feedback and wants to verify the remaining issues.\n"
        "\n"
        "Step 6: APPLY + VERIFY — codewalk_apply_and_verify_fix(session_id) applies all\n"
        "        accepted fixes AND runs static analysis + tests in one call. Persists\n"
        "        verification status (fixed/still_present) back to findings JSON.\n"
        "\n"
        "IMPORTANT: Do NOT carry findings in your context between batches.\n"
        "           Submit them with codewalk_submit_batch_findings, then forget them.\n"
        "           The summary tool will give you everything at the end.\n"
        "\n"
        "ALTERNATIVE: For non-review manual fixes (user says 'change X to Y'):\n"
        "        - codewalk_approve_action(proposed_action='...') → get token\n"
        "        - codewalk_apply_fix(file_path, old_code, new_code, approval_token) → apply\n"
        "        - codewalk_run_static_analysis + codewalk_run_tests → verify\n"
        "\n"
        "- codewalk_load_guidelines(docs_path) — load team coding standards/docs (run once per project)\n"
        "- codewalk_get_review_details(session_id) — retrieve a previous review context\n"
        "\n"
        "## ARCHITECTURE ANALYSIS\n"
        "- codewalk_get_architecture_health — bottlenecks, key files, circular dependencies, refactoring priorities\n"
        "- codewalk_call_chain(source, target) — trace the shortest import path between two files\n"
        "\n"
        "## DOCUMENTATION SEARCH\n"
        "- codewalk_index_docs(docs_path) — index a folder of .md/.pdf/.txt docs for semantic search\n"
        "- codewalk_search_docs(query) — search indexed docs, returns raw chunks for browsing\n"
        "- codewalk_ask_docs(question) — single search + formatted context for YOU to answer\n"
        "\n"
        "For broad doc questions, call codewalk_search_docs or codewalk_ask_docs 1-3 times\n"
        "with different phrasings and synthesize the merged chunks, just like codebase search.\n"
        "\n"
        "## QUERY ROUTING — pick the right tool first\n"
        "Route the user's question to the correct capability before calling a tool:\n"
        "\n"
        "- Architecture / module map / dependency direction / tech stack\n"
        "  → codewalk_get_overview, codewalk_get_execution_flow, codewalk_get_module_info\n"
        "\n"
        "- Specific function / method → codewalk_explain_function(name)\n"
        "- Specific class / component / type → codewalk_explain_class(name)\n"
        "- Symbol lookup / implementation details → codewalk_lookup_symbol, codewalk_search_codebase\n"
        "\n"
        "- Conventions / guidelines / commit rules / config / environment / process / docs\n"
        "  → codewalk_search_docs, codewalk_ask_docs\n"
        "\n"
        "- Impact of a change / blast radius / circular dependencies / call chains\n"
        "  → codewalk_get_blast_radius_map, codewalk_find_circular_dependencies, codewalk_call_chain\n"
        "\n"
        "- Code review / diff critique / check my changes\n"
        "  → codewalk_run_review (full diff review)\n"
        "  → codewalk_review_file(file_path) (single file deep review)\n"
        "\n"
        "- Accept/reject findings after review\n"
        "  → User edits llm_findings.json: set user_verdict to 'accepted' or 'rejected'\n"
        "\n"
        "- Re-review after addressing feedback (hides rejected findings)\n"
        "  → codewalk_re_review(target_branch?, staged?, commit?, refresh_stack?)\n"
        "\n"
        "- Apply accepted fixes from review\n"
        "  → codewalk_apply_and_verify_fix(session_id?) — apply + static analysis + tests in one step\n"
        "\n"
        "If unsure whether a question is about code or docs, prefer codewalk_search_docs for\n"
        "process/convention questions and codewalk_search_codebase for implementation questions.\n"
        "\n"
        "## VOICE COMPANION\n"
        "- codewalk_voice_ask — record mic + transcribe, then YOU:\n"
        "    1. Call the right codewalk tool\n"
        "    2. Show the FULL result as text in the chat (same detail as typed)\n"
        "    3. Call codewalk_speak() with a 2-4 sentence spoken summary\n"
        "- codewalk_speak(text) — speak a plain-English summary aloud via TTS\n"
        "\n"
        "## HUMAN-IN-THE-LOOP — GLOBAL RULE\n"
        "You call Codewalk over MCP; approval UX is provided by your host (not by Codewalk tools).\n"
        "\n"
        "For REVIEW FINDINGS (after codewalk_run_review):\n"
        "  - User edits llm_findings.json to set user_verdict ('accepted'/'rejected') per finding.\n"
        "  - Use codewalk_apply_and_verify_fix(session_id) to apply + verify in one step.\n"
        "  - No approve_action/token needed — the verdict IS the approval.\n"
        "\n"
        "For NON-REVIEW code changes (manual edits, user says 'change X to Y'):\n"
        "  1. Call codewalk_approve_action(proposed_action='<exactly what you will do>')\n"
        "  2. Present the output for the user — use the host approve/reject UI when available.\n"
        "  3. If approved — pass approval_token to codewalk_apply_fix. If rejected — do not apply.\n"
        "\n"
        "## PRESENTING BLAST RADIUS RESULTS\n"
        "When showing blast radius or overview results, separate files into two groups:\n"
        "1. **Core / Foundational** (design system, utils, extensions, config, constants,\n"
        "   shared components, theme files, base classes) — summarize briefly, e.g.\n"
        "   '12 design system files are high-risk as expected.'\n"
        "2. **Business Logic** (screens, pages, controllers, services, repositories,\n"
        "   state management, use cases, API clients) — show these in full detail.\n"
        "Lead with the Business Logic section — that's what the user cares about.\n"
        "Foundational files being high-risk is expected and not actionable.\n"
        "\n"
        "## SEARCH & CORRECTIVE RAG\n"
        "codewalk_search_codebase expands your query into 1-3 complementary search\n"
        "angles internally, retrieves raw code chunks for each, and returns the\n"
        "merged, deduplicated chunks. It does NOT return a pre-made answer — YOU\n"
        "must generate the answer from the chunks. Follow this flow:\n"
        "1. Call codewalk_search_codebase(query) — get filtered code chunks + confidence\n"
        "2. Read the chunks and generate your answer using ONLY the returned code\n"
        "3. If confidence < 0.3 or chunks seem irrelevant to the question:\n"
        "   - Rephrase the query (use different keywords, be more specific)\n"
        "   - Call codewalk_search_codebase again with the rephrased query\n"
        "   - Max 3 retries, then answer with best available chunks\n"
        "4. Always cite file paths and line numbers from chunk metadata\n"
        "\n"
        "## DEEP RESEARCH (for complex cross-cutting questions)\n"
        "When a question spans multiple modules or needs understanding how multiple\n"
        "parts connect (e.g. 'How does error handling work across the codebase?',\n"
        "'Explain the full auth flow end to end'), do NOT use a single search.\n"
        "Instead, follow this pattern:\n"
        "1. DECOMPOSE: Break the question into 3 or more independent sub-questions,\n"
        "   each targeting a different angle of the answer\n"
        "2. PARALLEL SEARCH: Call codewalk_search_codebase for EACH sub-question\n"
        "   simultaneously in one response (parallel tool calls)\n"
        "3. SYNTHESIZE: Merge all chunk results into one structured report:\n"
        "   - Summary of findings per sub-question\n"
        "   - Cross-references between sub-questions (patterns, shared files)\n"
        "   - Cite file paths and line numbers from chunk metadata\n"
        "4. SELF-CRITIQUE: Review your report — did you miss an angle?\n"
        "   If so, run one more codewalk_search_codebase for the gap\n"
        "\n"
        "Example: 'How does the payment retry system handle edge cases?'\n"
        "  → sq1: codewalk_search_codebase('payment retry logic implementation')\n"
        "  → sq2: codewalk_search_codebase('payment failure timeout conditions')\n"
        "  → sq3: codewalk_search_codebase('max retries exceeded dead letter queue')\n"
        "  → Synthesize all 3 into one report with citations\n"
        "\n"
        "## CLOUD INDEX MANAGEMENT\n"
        "Use these tools when CODEWALK_SERVER_URL + CODEWALK_REPO_NAME + CODEWALK_REPO_TOKEN are set:\n"
        "- codewalk_connect_repo(repo_name, repo_token) — ONE-STEP setup: detects git root,\n"
        "  validates origin remote, downloads index from cloud, extracts to .codewalk/,\n"
        "  updates mcp.json. Run this once after cloning a new repo.\n"
        "- codewalk_pull_index() — Download the latest cloud index (replaces local .codewalk/).\n"
        "  Returns 'Already up to date (vN)' if local version matches cloud — safe to call anytime.\n"
        "- codewalk_index_status() — Show local vs cloud version numbers, commit SHA,\n"
        "  file count, and how many versions behind local is.\n"
        "- codewalk_check_version() — Check if a newer Codewalk release is deployed on the\n"
        "  cloud server.\n"
        "\n"
        "## STALENESS NOTIFICATIONS (automatic)\n"
        "When cloud env vars are set, EVERY tool response may prepend banners if:\n"
        "  • Cloud index_version > local → run codewalk_pull_index\n"
        "  • Cloud API commit/version > local MCP → git pull origin master + restart MCP\n"
        "Also call codewalk_index_status() to compare local vs cloud index versions.\n"
        "\n"
        "## ERROR HANDLING\n"
        "If any tool returns a message starting with '❌' or 'Error:':\n"
        "- 'No index found' / 'INDEX EMPTY' → tell user to run codewalk_analyze_codebase first\n"
        "- 'Module not found' → show the available modules from the error message\n"
        "- 'Invalid approval' / 'missing or invalid approval' → you skipped codewalk_approve_action or used the wrong approval_token\n"
        "- Never retry the same tool with identical arguments after an error\n"
    ),
)

def _mcp_repo_path() -> str:
    """Return the repo path for MCP operations.

    Discovers the repo root by walking up from cwd looking for codewalk.yaml,
    creating a default one if missing. The MCP host usually launches the
    server with the workspace folder as cwd.
    """
    from src.codewalk.repo_discovery import ensure_codewalk_yaml
    return str(ensure_codewalk_yaml(create=True))


# Seed the shared state from the MCP workspace so query/cloud tools have a
# default repo path without requiring an explicit argument.
state.set_repo_path(_mcp_repo_path())


# For cloud support
def _target_repo_root() -> Path:
    """Git root where .codewalk/ lives — must match MCP workspace repo path."""
    return Path(_mcp_repo_path()).resolve()


def _local_manifest_path() -> Path:
    return _target_repo_root() / ".codewalk" / "manifest.json"


def _reset_state() -> None:
    """Clear in-memory state after the on-disk .codewalk/ index has changed.

    Forces the next tool call to re-initialize from the new index instead of
    using stale Chroma handles, module caches, or DuckDB connections.
    """
    if state._graph_store is not None:
        try:
            state._graph_store.close()
        except Exception:
            pass
        state._graph_store = None

    state._store = None
    state._modules_result = None
    state._deps = None
    state._files = None
    state._graph_runtime = None
    state._analyze_result = None


def _refresh_state_if_moved() -> None:
    """Re-discover the repo root from cwd and reset state if the workspace changed."""
    try:
        current = _mcp_repo_path()
    except Exception:
        return
    try:
        cached = state.get_repo_path()
    except RuntimeError:
        cached = None
    if cached != current:
        _log(f"[mcp] workspace changed: {cached} -> {current}; resetting state")
        state.set_repo_path(current)
        _reset_state()


def refresh_state(fn):
    """Decorator that refreshes repo state before tools that depend on it."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _refresh_state_if_moved()
        return fn(*args, **kwargs)
    return wrapper


def _is_cloud_configured() -> bool:
    """True when all cloud env vars required to download an index are present."""
    return all([
        os.getenv("CODEWALK_SERVER_URL"),
        os.getenv("CODEWALK_REPO_NAME"),
        os.getenv("CODEWALK_REPO_TOKEN"),
    ])


def download_cloud_index_if_missing():
    """Downloads cloud index into the MCP workspace's .codewalk/ if missing; shows banner if stale."""
    if not _is_cloud_configured():
        return None

    server_url = os.getenv("CODEWALK_SERVER_URL")
    repo_name  = os.getenv("CODEWALK_REPO_NAME")
    repo_token = os.getenv("CODEWALK_REPO_TOKEN")

    local_meta = _local_manifest_path()

    if not local_meta.exists():
        _download_index(server_url, repo_name, repo_token)
        return

    try:
        remote = requests.get(
            f"{server_url}/indexes/{repo_name}/manifest",
            headers={"X-Repo-Token": repo_token},
            timeout=5
        ).json()
    except Exception:
        return

    local = json.loads(local_meta.read_text())
    local_version = local.get("index_version", 0)
    remote_version = remote.get("index_version", 0)

    # Index staleness banners: every MCP tool via src/codewalk/staleness.py wrappers.
    _ = (local_version, remote_version, remote)


def _download_index(server_url: str, repo_name: str, repo_token: str) -> Path:
    import tempfile

    repo_root = _target_repo_root()
    _log(f"[download_index] Downloading index for {repo_name} → {repo_root}/.codewalk/ ...")
    request = requests.get(
        f"{server_url}/indexes/{repo_name}",
        headers={"X-Repo-Token": repo_token},
        stream=True, timeout=120,
    )

    request.raise_for_status()
    # Use a secure temp file instead of a predictable /tmp path
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        for chunk in request.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tarball = Path(tmp.name)

    try:
        codewalk_dir = repo_root / ".codewalk"
        if codewalk_dir.exists():
            shutil.rmtree(codewalk_dir)
        subprocess.run(["tar", "-xzf", str(tarball), "-C", str(repo_root)], check=True)
    finally:
        tarball.unlink(missing_ok=True)

    manifest_path = codewalk_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(
            f"Download succeeded but {manifest_path} was not found after extraction. "
            f"The archive may be empty or extracted to an unexpected location."
        )

    _log(f"[download_index] Index ready ({repo_name}) at {codewalk_dir}")
    return codewalk_dir

# ══════════════════════════════════════════════════════════════════════
#  Index gate — query tools load from disk; analyze builds the index
# ══════════════════════════════════════════════════════════════════════

def _require_index() -> str | None:
    """Return error message if no index on disk, else None (loads index automatically)."""
    try:
        if state.ensure_initialized():
            return None
    except RuntimeError as e:
        _log(f"[_require_index] {e}")
    return state.INDEX_REQUIRED_MCP


def _require_stack(tool_name: str = "") -> str | None:
    """Return error message if .codewalk/stack_context.json is missing, else None.

    Call this from any MCP tool that needs stack context to produce quality output.
    Returns a structured message telling the host how to set up stack context.
    The host follows the steps, then re-calls the original tool.
    """
    from src.codewalk.review.stack_detect import _load_cached

    try:
        repo_path = state.get_repo_path()
    except Exception:
        return None  # Can't check — let the tool handle repo errors itself

    if _load_cached(Path(repo_path)):
        return None  # Stack file exists — proceed

    resume_hint = f"Then re-call `{tool_name}` — it will proceed normally." if tool_name else "Then re-call the tool you were trying to use."

    return (
        f"## Stack Context Required\n\n"
        f"No `.codewalk/stack_context.json` found. Codewalk needs to know the "
        f"project's architecture, state management, and frameworks to produce "
        f"high-quality results.\n\n"
        f"**Steps:**\n"
        f"1. Call `codewalk_get_stack_info()` to get the file tree + detection prompt\n"
        f"2. Analyze it and produce a JSON describing the stack\n"
        f"3. Call `codewalk_save_stack_context(your_json)` to save it\n"
        f"4. {resume_hint}\n\n"
        f"This only happens **once per repo** — the file persists across all commits.\n"
        f"To refresh later: `codewalk_run_review(refresh_stack=True)` or call "
        f"`codewalk_get_stack_info()` + `codewalk_save_stack_context()` again."
    )


# ══════════════════════════════════════════════════════════════════════
#  SETUP TOOLS — user or AI runs these to onboard a codebase
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 1 [SETUP · user+AI]: codewalk_analyze_codebase ────────────
@mcp.tool()
def codewalk_analyze_codebase(mode: str = "auto") -> str:
    """Analyze a codebase structure and prepare for search.

    Modes:
      auto    — load complete index, do a full build if no index exists, or
                warn if the index is behind (default). Does NOT auto-resume.
      reindex — smart re-index (only changed/new/deleted files); use this to
                resume a partial/interrupted index
      full    — nuke everything and re-embed from scratch

    Call this once to set up a repo. Query tools work automatically after that.

    Flow:
    1. Local .codewalk/ index exists and is complete → load and return INDEX READY
    2. Local .codewalk/ index is partial (chroma has chunks but manifest missing
       or files missing) → warn and tell you to run reindex
    3. No local index + cloud configured → download cloud index
    4. No local index + local only → scan (codewalk.yaml excludes), embed locally

    ⏩ NEXT STEP: use any query tool directly
    """
    repo_path = _mcp_repo_path()
    _log(f"[codewalk_analyze_codebase] Starting analysis: {repo_path} mode={mode}")

    if mode not in {"auto", "reindex", "full"}:
        return f"❌ Invalid mode: '{mode}'. Use auto, reindex, or full."

    if not repo_path or not os.path.isdir(repo_path):
        return f"❌ Invalid repo path: '{repo_path}' is not a directory."

    state.set_repo_path(repo_path)
    docs_path = load_codewalk_yaml(repo_path).docs_path

    # Cloud: download index if missing; staleness checks run on every tool (staleness.py)
    download_cloud_index_if_missing()

    try:
        result = state.analyze_or_reindex_index(
            repo_path,
            docs_path=docs_path,
            mode=mode,
        )
    except Exception as e:
        error_msg = str(e)
        if "lock" in error_msg.lower() or "Could not set lock" in error_msg:
            return (
                f"Error: DuckDB lock conflict — another Codewalk process is using the database.\n\n"
                f"{error_msg}\n\n"
                f"Fix: Stop the other process (MCP server, API server, or CLI), then retry."
            )
        raise

    if not state._files:
        return (
            f"⚠️ No indexable files found after filtering.\n"
            f"Check codewalk.yaml indexing.exclude or .codewalkignore."
        )

    status = result.get("status")
    existing = state._store.chunk_count() if state._store else 0
    modules = []
    if state._modules_result is not None:
        modules = list(state._modules_result.get("modules", {}).keys())
    _log(f"[codewalk_analyze_codebase] Modules: {modules} | Status: {status} | Index: {existing} chunks")

    # Docs message
    docs_msg = ""
    if docs_path:
        from src.codewalk.doc_knowledge.doc_store import DocStore as _DocStore
        _ds = _DocStore(persist_dir=state.chroma_path(), collection_name=f"{state.get_collection_name()}_docs")
        _ds.create_collection()
        dc = _ds.chunk_count()
        if dc > 0:
            docs_msg = f"Docs: {dc} chunks embedded\n"

    if status == "ready":
        result_msg = (
            f"Codebase analyzed successfully.\n"
            + f"Files found: {len(state._files)}\n"
            + f"Modules found: {', '.join(modules)}\n"
            + f"Search index: INDEX READY — {existing} chunks available.\n"
            + f"{docs_msg}"
            + f"✅ Loaded existing index.\n"
            + f"Ready to answer questions — use query tools directly."
        )
    elif status == "behind":
        sample = result.get("missing_sample", [])
        sample_text = ""
        if sample:
            sample_text = "\nExamples: " + ", ".join(sample)
            if result["missing_count"] > len(sample):
                sample_text += f" and {result['missing_count'] - len(sample)} more"
        result_msg = (
            f"⚠️ Indexing is behind from repo.\n"
            + f"Files found: {len(state._files)}\n"
            + f"Indexed files: {len(state._files) - result['missing_count']}\n"
            + f"Missing files: {result['missing_count']}{sample_text}\n\n"
            + f"Run `codewalk_incremental_reindex` or `codewalk_analyze_codebase(mode='reindex')` to sync."
        )
    else:
        result_msg = (
            f"Codebase analyzed and indexed successfully.\n"
            + f"Files found: {len(state._files)}\n"
            + f"Files indexed: {result['files_scanned']}\n"
            + f"Chunks embedded: {result['chunks_embedded']}\n"
            + f"Time: {result['total_time']}\n"
            + f"{docs_msg}"
            + f"Modules found: {', '.join(modules)}\n\n"
            + f"✅ Local embedding complete — use query tools directly."
        )

    # If stack context file is missing, prompt the host to set it up now.
    # Skip this prompt when the index is behind so the user focuses on reindexing first.
    from src.codewalk.review.stack_detect import _load_cached
    if status != "behind" and not _load_cached(Path(repo_path)):
        result_msg += (
            "\n\n---\n\n"
            "## ⏩ Stack Context Setup Required\n\n"
            "To enable architecture-aware reviews and overview:\n"
            "1. Call `codewalk_get_stack_info()` to get the file tree + detection prompt\n"
            "2. Analyze it and produce the JSON describing the stack\n"
            "3. Call `codewalk_save_stack_context(your_json)` to save it\n\n"
            "This only happens **once per repo**."
        )

    return result_msg


# ─── TOOL 1b [SETUP · user+AI]: codewalk_generate_config ─────────────
@mcp.tool()
def codewalk_generate_config(force: bool = False) -> str:
    """Generate a starter codewalk.yaml for the current workspace.

    Detects the repo's tech stack and writes repo-specific exclusions.
    Core safety exclusions (node_modules, build artifacts, binaries, etc.)
    are applied automatically and are not duplicated in the file.

    Args:
        force: Overwrite an existing codewalk.yaml.

    Returns:
        Path to the generated file or a message if it already exists.
    """
    repo_path = _mcp_repo_path()
    if not repo_path or not os.path.isdir(repo_path):
        return f"❌ Invalid repo path: '{repo_path}' is not a directory."

    existing = os.path.join(repo_path, "codewalk.yaml")
    if os.path.exists(existing) and not force:
        return (
            f"⚠️ codewalk.yaml already exists at {existing}.\n"
            "Pass force=True to overwrite, or edit the file directly."
        )

    path = generate_codewalk_yaml(repo_path, force=force)
    if path:
        return f"✅ Generated codewalk.yaml at {path}"
    return f"⚠️ codewalk.yaml already exists at {existing}."


# ══════════════════════════════════════════════════════════════════════
#  QUERY TOOLS — user asks a question, AI picks the right tool
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 2 [QUERY · user+AI]: codewalk_search_codebase ──────────────
@mcp.tool()
def codewalk_search_codebase(query: str) -> str:
    """Search the codebase and return relevant code chunks for analysis.

    Performs one semantic search using symbol lookup, similarity, distance
    filtering, keyword grading, and graph expansion. Returns raw context for
    the host LLM to analyze.

    For every question, the host should call this tool 1-3 times
    with different phrasings and synthesize the merged chunks into one answer.

    For a specific function/method by name, prefer codewalk_explain_function.
    For a specific class/component/type by name, prefer codewalk_explain_class.
    For raw symbol chunks, prefer codewalk_lookup_symbol.

    Requires codewalk_analyze_codebase + indexing workflow first.

    Args:
        query: Natural language question, e.g. "how does authentication work",
               "error handling in API routes", "how files get chunked"
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_search_codebase] Query: {query}")

    chunks, confidence, retrieval_good = deterministic_search(
        query,
        state._store,
        graph_store=state._graph_store,
        n_results=5,
        use_graph_expansion=True,
    )

    if not chunks:
        return (
            f"No relevant chunks found for: {query}\n"
            f"Confidence: {confidence:.2f}\n\n"
            f"Try rephrasing with different keywords."
        )

    context = format_context(chunks)
    meta = (
        f"\n\n---\n"
        f"Confidence: {confidence:.2f} | "
        f"Retrieval good: {retrieval_good} | "
        f"Chunks: {len(chunks)}\n"
    )
    prompt = SYSTEM_PROMPT + "\n" + QUESTION_PROMPT.format(
        context=context, question=query
    )
    return prompt + meta


# ─── TOOL 3 [QUERY · user+AI]: codewalk_get_module_info ──────────────
@mcp.tool()
def codewalk_get_module_info(module_name: str) -> str:
    """Get module or feature details — files, symbols, dependencies.

    Returns: file list with extracted function/class symbols (name, type, line range),
    module dependencies, and which other modules depend on this one.

    If the name isn't a top-level module, automatically searches for it as a
    sub-folder (feature) inside modules. For example, "users" resolves to
    "features/users" if it exists.

    Requires codewalk_analyze_codebase + indexing workflow first.

    Args:
        module_name: Name of the module or feature — any top-level module or sub-folder name
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_get_module_info] Module: {module_name}")
    modules = state._modules_result["modules"]
    module_graph = state._modules_result.get("module_graph", {})

    actual_name, info, matched_as_feature = resolve_module_with_fallback(
        module_name, state._modules_result, files=state._files
    )

    if actual_name is None:
        return module_not_found_error(module_name, modules) + "\n\nTip: Try the parent module name (e.g. 'features' instead of a specific feature)."

    depends_on = module_graph.get(actual_name, [])
    depended_by = [n for n, deps in module_graph.items() if actual_name in deps]
    lang_str = ", ".join(f"{l} ({c})" for l, c in sorted(info["languages"].items()))

    # Get symbols from ChromaDB for each file (skip for large modules — ChromaDB has query limits)
    file_list = sorted(info["files"])
    symbols_by_file = {}
    if state._store is not None and hasattr(state._store, 'get_symbols_by_files') and len(file_list) <= 100:
        symbols_by_file = state._store.get_symbols_by_files(file_list)

    # Build per-file detail lines (cap at 50 files to keep output readable)
    file_lines = []
    display_files = file_list[:50]
    for file_path in display_files:
        name = short_name(file_path)
        symbols = symbols_by_file.get(file_path, [])
        if symbols:
            sym_parts = []
            for s in symbols:
                sym_parts.append(f"`{s['symbol_name']}` ({s['symbol_type']}, L{s['start_line']}-{s['end_line']})")
            file_lines.append(f"- **{name}**: {', '.join(sym_parts)}")
        else:
            file_lines.append(f"- **{name}**: *(not indexed or no named symbols)*")

    if len(file_list) > 50:
        file_lines.append(f"\n*... and {len(file_list) - 50} more files. Use a sub-folder name to drill deeper.*")

    files_section = "\n".join(file_lines)

    # Header differs for feature vs module
    if matched_as_feature:
        header = f"## Feature: {module_name} (inside '{actual_name}' module)\n"
    else:
        header = f"## Module: {actual_name}\n"

    # List sub-folders (features) when showing a large module
    sub_features_section = ""
    if not matched_as_feature and info["file_count"] > 30:
        source_root = state._modules_result.get("source_root", "")
        prefix = f"{source_root}/{actual_name}/" if source_root else f"{actual_name}/"
        sub_folders = set()
        for f in info["files"]:
            if f.startswith(prefix):
                relative = f[len(prefix):]
                parts = relative.split("/")
                if len(parts) > 1:
                    sub_folders.add(parts[0])
        if len(sub_folders) >= 3:
            sorted_subs = sorted(sub_folders)
            sub_features_section = f"\n\n### Sub-folders ({len(sorted_subs)})\n" + ", ".join(sorted_subs)
            sub_features_section += f"\n\n*Tip: Call `codewalk_get_module_info(\"{sorted_subs[0]}\")` to drill into a specific sub-folder.*"

    description_section = ""

    return (
        f"{header}"
        f"**Files:** {info['file_count']}\n"
        f"**Languages:** {lang_str}\n"
        f"**Depends on:** {', '.join(depends_on) or 'None (standalone)'}\n"
        f"**Depended on by:** {', '.join(depended_by) or 'None'}"
        f"{description_section}\n"
        f"### Files & Symbols\n{files_section}"
        f"{sub_features_section}"
    )

# ─── TOOL 4 [QUERY · user+AI]: codewalk_explain_function ─────────────
@mcp.tool()
def codewalk_explain_function(function_name: str) -> str:
    """Look up a function/class in Codewalk's index and explain it with blast radius.

    Uses ChromaDB symbol search + the dependency graph to return:
    1. Source code from the indexed embeddings
    2. Blast radius — which files break if this symbol changes

    Args:
        function_name: Exact name of the function, method, or class,
                       e.g. "scan_directory", "VectorStore", "embed_chunks"
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_explain_function] Looking up: {function_name}")
    return explain_function_text(state._store, function_name, state._deps, state._graph_runtime, state._graph_store)


# ─── TOOL 4a [QUERY · user+AI]: codewalk_explain_class ─────────────────
@mcp.tool()
def codewalk_explain_class(class_name: str) -> str:
    """Look up a class/component/type in Codewalk's index and explain it with blast radius.

    Internally uses the same symbol-resolution logic as codewalk_explain_function;
    this is the class-specific entry point for clearer routing.

    Uses ChromaDB symbol search + the dependency graph to return:
    1. Source code from the indexed embeddings
    2. Blast radius — which files break if this class changes

    Args:
        class_name: Exact name of the class, component, or type,
                    e.g. "Button", "VectorStore", "Carousel"
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_explain_class] Looking up: {class_name}")
    return explain_function_text(state._store, class_name, state._deps, state._graph_runtime, state._graph_store)


# ─── TOOL 4b [QUERY · user+AI]: codewalk_lookup_symbol ───────────────
@mcp.tool()
def codewalk_lookup_symbol(
    query: str,
    include_callers: bool = True,
    include_callees: bool = False,
) -> str:
    """Deterministic symbol lookup: find symbols in the query and return their chunks.

    Uses the DuckDB knowledge graph + ChromaDB. No LLM is called.
    Useful when you know a function/class name or suspect one is mentioned
    in the question.

    Args:
        query: Question or symbol name, e.g. "scan_directory" or
               "where is get_user defined".
        include_callers: Also return chunks for caller symbols.
        include_callees: Also return chunks for callee symbols.
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_lookup_symbol] Query: {query}")
    chunks = deterministic_symbol_lookup(
        query,
        state._store,
        state._graph_store,
        include_callers=include_callers,
        include_callees=include_callees,
    )

    if not chunks:
        return f"No symbols matched for: '{query}'"

    lines = [f"## Symbol Lookup: '{query}'\n"]
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        file_path = meta.get("file_path", "?")
        symbol = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)
        header = f"### Result {i}: {file_path}"
        if symbol:
            header += f" | {symbol_type or 'symbol'}: {symbol}"
        if start_line:
            header += f" (lines {start_line}-{end_line})"
        lines.append(header)
        lines.append(f"```\n{chunk.get('text', '')[:3000]}\n```")

    return "\n\n".join(lines)


# ─── TOOL 5 [QUERY · user+AI]: codewalk_get_overview ─────────────────
@mcp.tool()
def codewalk_get_overview() -> str:
    """Get the project overview from Codewalk's computed analysis.

    Returns:
    - Architecture context (from .codewalk/stack_context.json)
    - Module list with file counts and languages
    - Module dependency flow (entry points → core modules)
    - Top 30 riskiest files by blast radius with break chains

    Requires .codewalk/stack_context.json for full output. If missing, returns
    instructions to set it up (one-time per repo).
    """
    if err := _require_index():
        return err
    if err := _require_stack("codewalk_get_overview()"):
        return err

    _log("[codewalk_get_overview] Generating overview...")
    from src.codewalk.review.stack_detect import _load_cached, format_stack_context_header
    repo = Path(state.get_repo_path())
    cached_stack = _load_cached(repo) or {}
    stack_header = format_stack_context_header(cached_stack)
    overview = overview_text(state.get_repo_path(), state._modules_result, state._deps, state._graph_runtime)
    if stack_header:
        return stack_header + "\n" + overview
    return overview

# ─── TOOL 6 [QUERY · user+AI]: codewalk_get_blast_radius_map ─────────
@mcp.tool()
def codewalk_get_blast_radius_map(target: str = "") -> str:
    """Get the blast radius (change risk) for files in the codebase.

    Shows which files would break if you change each file.
    Use this when the user asks about risk, impact, or "what breaks if I change X".

    Args:
        target: A module name (e.g. "analysis"), a file name (e.g. "scanner.py"),
                or empty for the top 30 riskiest files across the whole repo.
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_get_blast_radius_map] Target: {target or 'top 30'}")
    return blast_radius_map_text(state._modules_result, state._deps, target, state._graph_runtime)

# ─── TOOL 6b [QUERY · user+AI]: codewalk_find_circular_dependencies
@mcp.tool()
def codewalk_find_circular_dependencies() -> str:
    """Find circular dependencies in the indexed codebase.

    Returns strongly-connected cycle groups and suggested edges to break.
    Requires codewalk_analyze_codebase + indexing workflow first.
    """
    if err := _require_index():
        return err

    _log("[codewalk_find_circular_dependencies]")
    runtime = state.get_graph_runtime()
    cycles = runtime.detect_cycles()
    if not cycles.get("has_cycles"):
        return "## Circular Dependencies\nNo circular dependencies detected in the indexed files."

    lines = ["## Circular Dependencies Detected\n"]
    for i, group in enumerate(cycles["cycle_groups"], 1):
        lines.append(f"### Cycle group {i} ({len(group)} files)")
        for f in sorted(group):
            lines.append(f"- `{f}`")
        lines.append("")

    if cycles.get("edges_to_break"):
        lines.append("### Suggested edges to break")
        for src, dst in cycles["edges_to_break"][:20]:
            lines.append(f"- `{src}` → `{dst}`")

    return "\n".join(lines)


# ─── TOOL 7 [QUERY · user+AI]: codewalk_get_reading_order ────────────
@mcp.tool()
def codewalk_get_reading_order(module_name: str = "") -> str:
    """Get the recommended reading order for the codebase.

    Returns ALL files in dependency order (read dependencies first).
    Each file shows its position, dependency info, and blast radius risk.
    Requires codewalk_analyze_codebase + indexing workflow first.

    Args:
        module_name: Optional. Scope to a specific module, e.g. "analysis".
                     If empty, returns order for the entire repo.
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_get_reading_order] module={module_name or 'all'}")
    return reading_order_text(state._files, state._deps, state._modules_result, module_name, state._graph_runtime)


# ─── TOOL 8 [QUERY · user+AI]: codewalk_get_execution_flow ───────────
@mcp.tool()
def codewalk_get_execution_flow(module_name: str = "") -> str:
    """Get the execution flow showing how code connects.

    Without module_name: returns module-to-module flow (which modules
    depend on which) plus entry point modules.
    With module_name: returns file-to-file flow within that module
    (which files import which files inside that module).

    Args:
        module_name: Optional. Show file-level flow inside this module.
                     If empty, shows module-level flow for the whole repo.
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_get_execution_flow] module={module_name or 'repo-level'}")
    return execution_flow_text(state._modules_result, state._deps, module_name)


# ══════════════════════════════════════════════════════════════════════
#  MAINTENANCE TOOLS — user or AI can call after code changes
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 9 [MAINT · user+AI]: codewalk_incremental_reindex ────────
@mcp.tool()
def codewalk_incremental_reindex() -> str:
    """Re-index only files that changed since last indexing.

    Compares content hashes stored in ChromaDB metadata against current
    file content on disk. Skips unchanged files, re-embeds changed ones,
    removes chunks for deleted files, and resumes a partial/interrupted index.
    Much faster than full re-index.

    If no local index exists, this performs a full build. If a partial index
    exists (chroma has chunks but manifest is missing), it backfills the
    missing files instead of returning "No index found".

    Returns a summary showing how many files were skipped, re-indexed,
    or deleted, plus the number of new chunks embedded.

    ⏪ PREVIOUS STEP: codewalk_analyze_codebase (first-time setup)
    ⏩ NEXT STEP: any query tool (search, explain, blast radius, etc.)
    """
    repo_path = _mcp_repo_path()
    if not repo_path or not os.path.isdir(repo_path):
        return f"❌ Invalid repo path: '{repo_path}' is not a directory."

    state.set_repo_path(repo_path)
    docs_path = load_codewalk_yaml(repo_path).docs_path

    result = state.analyze_or_reindex_index(
        repo_path,
        docs_path=docs_path,
        mode="reindex",
    )

    return (
        f"Incremental reindex complete ({result['total_time']})\n\n"
        f"  Files on disk:   {result['files_scanned']}\n"
        f"  Skipped (same):  {result['files_skipped']}\n"
        f"  Re-indexed:      {result['files_reindexed']}\n"
        f"  Deleted:         {result['files_deleted']}\n"
        f"  Chunks embedded: {result['chunks_embedded']}\n\n"
        f"Analysis cache refreshed. Docs/guidelines were not re-indexed; use codewalk_index_docs(path) to refresh docs."
    )


# ─── TOOL 10 [MAINT · user+AI]: codewalk_refresh_analysis ────────────
@mcp.tool()
def codewalk_refresh_analysis() -> str:
    """Refresh the cached analysis without re-embedding.

    Re-scans files, rebuilds dependency graph, and re-detects modules.
    Use this after code changes to update blast radius, reading order,
    and module structure. Does NOT re-index or re-embed — embeddings
    stay as they are. For re-embedding, use the full setup workflow.
    """
    if err := _require_index():
        return err

    _log("[codewalk_refresh_analysis] Refreshing cached analysis...")
    state.rebuild_analysis_cache()

    modules = list(state._modules_result["modules"].keys())
    return (
        f"Analysis refreshed (no re-embedding).\n"
        f"Files: {len(state._files)}\n"
        f"Dependency graph: {len(state._deps['graph'])} files\n"
        f"Modules: {', '.join(modules)}"
    )


def _finding_id_from_dict(finding: dict) -> str:
    """Compute a stable ID for a raw finding dict submitted by the host LLM."""
    import hashlib
    import re

    def _normalize(text: str) -> str:
        return " ".join(text.lower().split())

    def _extract_anchor(snippet: str | None) -> str | None:
        if not snippet:
            return None
        patterns = [
            r"^\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            r"^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)",
            r"^\s*(?:public|private|protected|static|async)?\s*(?:function\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
            r"^\s*(?:void|int|String|bool|Future|Widget|[a-zA-Z_][a-zA-Z0-9_<>.\[\]]*)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
        ]
        for line in snippet.splitlines():
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    return match.group(1)
        return None

    snippets = [finding.get("current_code")]
    for ev in finding.get("evidence") or []:
        snippets.append(ev.get("snippet"))

    anchor = None
    for snippet in snippets:
        anchor = _extract_anchor(snippet)
        if anchor:
            break

    if not anchor:
        title = _normalize(finding.get("title", ""))
        title = re.sub(r"\b[a-z_][a-z0-9_]{0,2}\b", "", title)
        title = re.sub(r"\d+", "", title)
        anchor = "|".join([_normalize(title), finding.get("file_path", "")])

    key = "|".join([
        finding.get("category", "bug"),
        finding.get("file_path", ""),
        anchor.lower(),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class _NoChangesError(Exception):
    """Raised when there are no diff files to review."""


class _StackRequiredError(Exception):
    """Raised when stack context is missing and must be provided by the host."""

    def __init__(self, target_branch: str | None, staged: bool, commit: str | None):
        self.target_branch = target_branch
        self.staged = staged
        self.commit = commit


def _start_batched_review(
    repo: Path,
    target_branch: str | None,
    staged: bool,
    commit: str | None,
    refresh_stack: bool,
) -> dict:
    """Shared setup for a batched review session.

    Returns a dict with session, session_dir, batch_state, static_result,
    batches, rubrics, stack_header, stack, diff_files, and auto_f.
    """
    from src.codewalk.review.engine import (
        _build_common_context,
        _build_static_findings,
        group_files_for_review,
    )
    from src.codewalk.review.rubric_loader import build_rubrics
    from src.codewalk.review.stack_detect import (
        _load_cached,
        format_stack_context_header,
        get_rubric_names_from_stack,
    )
    from src.codewalk.review.session import ReviewSession, SessionStatus
    from src.codewalk.review.session_store import save_session, _session_dir
    from src.codewalk.review.utils import build_session_folder_name, get_current_branch
    from src.codewalk.review.renderers.markdown import render_findings_markdown
    from src.codewalk.codewalk_config import load_codewalk_yaml
    from datetime import datetime, timezone
    import json as _json

    codewalk_yaml = load_codewalk_yaml(str(repo))

    # Layer 0: deterministic context (once for all files)
    static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree = _build_common_context(
        repo, target_branch, commit, staged, codewalk_yaml,
    )

    if not diff_files:
        raise _NoChangesError()

    # Stack detection for MCP path:
    # 1. Check .codewalk/stack_context.json (persistent across commits)
    # 2. If missing (or refresh_stack=True), tell host to call
    #    codewalk_get_stack_info -> codewalk_save_stack_context -> re-call us
    cached_stack = None if refresh_stack else _load_cached(repo)
    if not cached_stack:
        raise _StackRequiredError(target_branch, staged, commit)

    stack = {k: v for k, v in cached_stack.items() if not k.startswith("_")}
    stack_header = format_stack_context_header(stack)
    rubric_names = get_rubric_names_from_stack(stack)
    rubrics = build_rubrics(repo, {df.file_path for df in diff_files}, detected_rubric_names=rubric_names)

    # Group files into batches
    batches = group_files_for_review(
        diff_files,
        risk_annotations=static_result.risk_annotations,
        max_per_batch=5,
    )

    # Create session with batch queue
    session_id = ReviewSession.generate_id()
    current_branch = get_current_branch(repo)
    created_at = datetime.now(timezone.utc)
    folder_name = build_session_folder_name(created_at, current_branch, target_branch)

    # Store batch queue in session
    batch_queue = [[df.file_path for df in batch] for batch in batches]

    session = ReviewSession(
        session_id=session_id,
        repo_path=str(repo),
        target_branch=target_branch,
        commit=commit,
        staged=staged,
        status=SessionStatus.ACTIVE,
        folder_name=folder_name,
        current_branch=current_branch,
        created_at=created_at.isoformat(),
        updated_at=created_at.isoformat(),
    )
    save_session(session)

    # Save batch queue to session folder
    session_dir = _session_dir(repo, folder_name)
    session_dir.mkdir(parents=True, exist_ok=True)

    batch_state = {
        "session_id": session_id,
        "total_files": len(diff_files),
        "total_batches": len(batches),
        "current_batch_index": 0,
        "batch_queue": batch_queue,
        "target_branch": target_branch,
        "commit": commit,
        "staged": staged,
        "stack_header": stack_header,
        "rubric_core": rubrics.core,
        "rubric_language": rubrics.language,
        "rubric_framework": rubrics.framework,
        "rubric_fallback": rubrics.fallback,
    }
    (session_dir / "batch_state.json").write_text(
        _json.dumps(batch_state, indent=2), encoding="utf-8"
    )

    # Write static_findings.json — deterministic findings persisted once
    auto_f = _build_static_findings(static_result)
    static_findings_data = [f.to_dict() for f in auto_f]
    (session_dir / "static_findings.json").write_text(
        _json.dumps(static_findings_data, indent=2),
        encoding="utf-8",
    )
    (session_dir / "static_findings.md").write_text(
        render_findings_markdown(
            static_findings_data,
            title="Static Findings",
            source_label="deterministic static analysis",
        ),
        encoding="utf-8",
    )

    # Initialize empty llm_findings.json for host LLM to append to
    (session_dir / "llm_findings.json").write_text("[]", encoding="utf-8")
    (session_dir / "llm_findings.md").write_text(
        render_findings_markdown([], title="LLM Findings", source_label="review LLM"),
        encoding="utf-8",
    )

    return {
        "session": session,
        "session_dir": session_dir,
        "batch_state": batch_state,
        "static_result": static_result,
        "batches": batches,
        "rubrics": rubrics,
        "stack_header": stack_header,
        "stack": stack,
        "diff_files": diff_files,
        "auto_f": auto_f,
    }


# ─── TOOL 11 [MAINT · user+AI]: codewalk_run_review ─────────
@mcp.tool()
def codewalk_run_review(
    target_branch: str | None = None,
    staged: bool = False,
    commit: str | None = None,
    refresh_stack: bool = False,
) -> str:
    """Start a batched review and return the first batch of files for review.

    Codewalk runs deterministic analysis (Layer 0: git diff, risk annotations,
    graph analysis), groups files into batches of 3-5 related files, and returns
    the first batch with full context for you to review.

    After reviewing the first batch, call codewalk_review_next_batch(session_id)
    repeatedly to get the remaining batches until all files are reviewed.

    By default, reviews ALL local changes: staged, unstaged, AND new untracked
    files. No flags needed — "review my changes" means everything.

    Args:
        target_branch: Diff working tree against this branch (e.g. "main").
            Shows committed + staged + unstaged + untracked changes.
            If None, reviews local changes since the last commit.
        staged: If True, review ONLY staged changes (narrow mode). No untracked
            files. This is the only escape hatch for a narrower diff.
        commit: Review a specific commit by SHA or ref. Historical snapshot —
            no untracked files.
        refresh_stack: If True, ignore the existing .codewalk/stack_context.json
            and re-prompt for stack detection. Use when the project's tech stack
            has changed (e.g. new framework added).

    Returns:
        Session info + first batch context (diff + file content + risk + rubric).
    """
    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available. Run codewalk_analyze_codebase first."

    index_ready = False
    try:
        index_ready = state.ensure_initialized()
    except Exception as e:
        _log(f"[codewalk_run_review] ensure_initialized failed: {e}")

    warning = ""
    if not index_ready:
        warning = (
            "ℹ️ **Dependency graph built on-the-fly** (~5s first review, instant after).\n\n"
            "Review includes blast radius, PageRank, cycle detection, and bottleneck analysis. "
            "For additional neighborhood context (callers, tests), run `codewalk_analyze_codebase`.\n\n"
            "---\n\n"
        )

    try:
        repo = Path(repo_path)

        result = _start_batched_review(repo, target_branch, staged, commit, refresh_stack)

    except _NoChangesError:
        return warning + "✅ No changes found to review."
    except _StackRequiredError as e:
        # No stack context file — host must fill it before we can proceed
        review_args = f"target_branch='{e.target_branch or ''}'"
        if e.staged:
            review_args += ", staged=True"
        if e.commit:
            review_args += f', commit="{e.commit}"'

        return (
            f"{warning}"
            f"## Stack Context Required\n\n"
            f"No `.codewalk/stack_context.json` found. Codewalk needs to know the "
            f"project's architecture, state management, and frameworks to load the "
            f"correct review rubrics.\n\n"
            f"**Steps:**\n"
            f"1. Call `codewalk_get_stack_info()` to get the file tree + prompt\n"
            f"2. Analyze it and produce a JSON describing the stack\n"
            f"3. Call `codewalk_save_stack_context(your_json)` to save it\n"
            f"4. Call `codewalk_run_review({review_args})` again to start the review\n\n"
            f"This only happens **once per repo** — the file persists across all commits.\n"
            f"To refresh later: `codewalk_run_review({review_args}, refresh_stack=True)`"
        )
    except Exception as e:
        _log(f"[codewalk_run_review] error: {e}")
        return warning + f"❌ Review failed: {e}"

    session = result["session"]
    batches = result["batches"]
    static_result = result["static_result"]
    stack_header = result["stack_header"]
    rubrics = result["rubrics"]
    stack = result["stack"]
    diff_files = result["diff_files"]
    auto_f = result["auto_f"]

    # Build first batch context
    first_batch_context = _build_batch_context_for_host(
        repo, batches[0], static_result, stack_header, rubrics,
    )

    lines = [warning]
    lines.append(f"# Review Session: `{session.session_id}`\n")
    lines.append(f"- **{len(diff_files)} files** in **{len(batches)} batches** (3-5 files each, grouped by feature)")
    lines.append(f"- Stack: {', '.join(stack.get('languages', []))} + {', '.join(stack.get('frameworks', []))}")
    lines.append(f"- Branch: `{session.current_branch}` → `{target_branch or 'working tree'}`")
    if auto_f:
        lines.append(f"- **{len(auto_f)} architectural warnings** (high-impact files detected)")
    lines.append("")
    lines.append(f"## Batch 1/{len(batches)}\n")
    lines.append(first_batch_context)
    lines.append("")
    lines.append("---")
    lines.append("**After reviewing this batch:**")
    lines.append(f"1. Call `codewalk_submit_batch_findings('{session.session_id}', findings=[...])` with your findings")
    lines.append(f"2. Call `codewalk_review_next_batch('{session.session_id}')` to get the next batch")
    lines.append(f"**{len(batches) - 1} batches remaining.**")

    return "\n".join(lines)


# ─── TOOL 11a [REVIEW · AI]: codewalk_re_review ─────────
@mcp.tool()
def codewalk_re_review(
    target_branch: str | None = None,
    staged: bool = False,
    commit: str | None = None,
    refresh_stack: bool = False,
) -> str:
    """Start a fresh review while hiding findings the user rejected in the last session.

    This is useful after the user has accepted/rejected findings from a previous
    codewalk_run_review: it creates a new review session, re-scans the diff, and
    suppresses any finding whose ID matches a previously rejected finding. New or
    still-present issues will be reported normally.

    Args:
        target_branch: Diff working tree against this branch (e.g. "main").
            Shows committed + staged + unstaged + untracked changes.
            If None, reviews local changes since the last commit.
        staged: If True, review ONLY staged changes (narrow mode).
        commit: Review a specific commit by SHA or ref.
        refresh_stack: If True, ignore the existing .codewalk/stack_context.json
            and re-prompt for stack detection.

    Returns:
        New session info + first batch context. Rejected findings from the previous
        session are filtered out of the final summary.
    """
    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available. Run codewalk_analyze_codebase first."

    index_ready = False
    try:
        index_ready = state.ensure_initialized()
    except Exception as e:
        _log(f"[codewalk_re_review] ensure_initialized failed: {e}")

    warning = ""
    if not index_ready:
        warning = (
            "ℹ️ **Dependency graph built on-the-fly** (~5s first review, instant after).\n\n"
            "Review includes blast radius, PageRank, cycle detection, and bottleneck analysis. "
            "For additional neighborhood context (callers, tests), run `codewalk_analyze_codebase`.\n\n"
            "---\n\n"
        )

    try:
        from src.codewalk.review.session_store import (
            find_last_session,
            load_findings,
        )

        repo = Path(repo_path)

        # Find the most recent review session for this branch
        previous_session = find_last_session(repo, target_branch)
        if previous_session is None:
            return (
                f"{warning}❌ No previous review session found for "
                f"branch `{target_branch or 'current'}`. Run `codewalk_run_review` first."
            )

        previous_folder = previous_session.folder_name or previous_session.session_id
        previous_findings = load_findings(repo, previous_folder)
        rejected_ids = {
            f.get("id") for f in previous_findings
            if f.get("user_verdict") == "rejected" and f.get("id")
        }

        result = _start_batched_review(repo, target_branch, staged, commit, refresh_stack)

    except _NoChangesError:
        return warning + "✅ No changes found to re-review."
    except _StackRequiredError as e:
        review_args = f"target_branch='{e.target_branch or ''}'"
        if e.staged:
            review_args += ", staged=True"
        if e.commit:
            review_args += f', commit="{e.commit}"'

        return (
            f"{warning}"
            f"## Stack Context Required\n\n"
            f"No `.codewalk/stack_context.json` found. Codewalk needs to know the "
            f"project's architecture, state management, and frameworks to load the "
            f"correct review rubrics.\n\n"
            f"**Steps:**\n"
            f"1. Call `codewalk_get_stack_info()` to get the file tree + prompt\n"
            f"2. Analyze it and produce a JSON describing the stack\n"
            f"3. Call `codewalk_save_stack_context(your_json)` to save it\n"
            f"4. Call `codewalk_re_review({review_args})` again to start the re-review\n\n"
            f"This only happens **once per repo** — the file persists across all commits.\n"
            f"To refresh later: `codewalk_re_review({review_args}, refresh_stack=True)`"
        )
    except Exception as e:
        _log(f"[codewalk_re_review] error: {e}")
        return warning + f"❌ Re-review failed: {e}"

    session = result["session"]
    session_dir = result["session_dir"]
    batch_state = result["batch_state"]
    batches = result["batches"]
    static_result = result["static_result"]
    stack_header = result["stack_header"]
    rubrics = result["rubrics"]
    stack = result["stack"]
    diff_files = result["diff_files"]
    auto_f = result["auto_f"]

    # Persist re-review linkage and rejected IDs
    batch_state["previous_session_id"] = previous_session.session_id
    batch_state["rejected_ids"] = sorted(rejected_ids)
    (session_dir / "batch_state.json").write_text(
        json.dumps(batch_state, indent=2), encoding="utf-8"
    )

    # Build first batch context
    first_batch_context = _build_batch_context_for_host(
        repo, batches[0], static_result, stack_header, rubrics,
    )

    lines = [warning]
    lines.append(f"# Re-Review Session: `{session.session_id}`\n")
    lines.append(f"- **{len(diff_files)} files** in **{len(batches)} batches** (3-5 files each, grouped by feature)")
    lines.append(f"- Stack: {', '.join(stack.get('languages', []))} + {', '.join(stack.get('frameworks', []))}")
    lines.append(f"- Branch: `{session.current_branch}` → `{target_branch or 'working tree'}`")
    if auto_f:
        lines.append(f"- **{len(auto_f)} architectural warnings** (high-impact files detected)")
    if rejected_ids:
        lines.append(f"- **{len(rejected_ids)} previously rejected finding(s)** will be hidden in the summary")
    lines.append("")
    lines.append(f"## Batch 1/{len(batches)}\n")
    lines.append(first_batch_context)
    lines.append("")
    lines.append("---")
    lines.append("**After reviewing this batch:**")
    lines.append(f"1. Call `codewalk_submit_batch_findings('{session.session_id}', findings=[...])` with your findings")
    lines.append(f"2. Call `codewalk_review_next_batch('{session.session_id}')` to get the next batch")
    lines.append(f"**{len(batches) - 1} batches remaining.**")

    return "\n".join(lines)


def _build_batch_context_for_host(
    repo_path: Path,
    batch: list,
    static_result,
    stack_header: str,
    rubrics,
) -> str:
    """Build review context markdown for a batch of files."""
    from src.codewalk.review.neighborhood import expand_neighborhood
    from src.codewalk.review.utils import smart_truncate_file_content
    from src.codewalk.review.engine import _load_graph_runtime

    parts: list[str] = []

    # Stack context (same for all batches — host caches this)
    if stack_header:
        parts.append(stack_header)

    # Rubric (once per batch)
    parts.append("## Review Rubric\n")
    if rubrics.core:
        parts.append(rubrics.core)
    lang_parts = [r for _, r in sorted(rubrics.language.items())]
    if lang_parts:
        parts.append("\n".join(lang_parts))
    if rubrics.framework:
        parts.append(rubrics.framework)
    if rubrics.fallback:
        parts.append(rubrics.fallback)
    parts.append("")

    # Neighborhood for this batch
    graph_runtime, owns = _load_graph_runtime(repo_path)
    graph_store = graph_runtime.store if graph_runtime and hasattr(graph_runtime, "store") else None
    try:
        neighborhood = expand_neighborhood(repo_path, batch, graph_store=graph_store, max_tokens=15_000)
    finally:
        if owns and graph_runtime and hasattr(graph_runtime, "store"):
            try:
                graph_runtime.store.close()
            except Exception:
                pass

    # Per-file context
    for df in batch:
        ra = static_result.risk_annotations.get(df.file_path)
        parts.append(f"### {df.file_path} (+{df.added_lines}/-{df.removed_lines})")
        if ra and ra.to_prompt_text():
            parts.append(f"> {ra.to_prompt_text()}")
        parts.append("")

        # File content (smart truncated)
        full_path = repo_path / df.file_path
        content = ""
        if full_path.exists():
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                pass
        if content:
            truncated = smart_truncate_file_content(content, df.hunks, max_tokens=4000)
            parts.append("```")
            parts.append(truncated)
            parts.append("```")
        else:
            parts.append("*(file deleted or not found)*")

        # Diff hunks
        parts.append("\n**Diff:**")
        for hunk in df.hunks:
            parts.append(f"```diff")
            parts.append(f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.start_line},{len(hunk.lines)} @@")
            for line in hunk.lines:
                prefix = {"added": "+", "removed": "-", "context": " "}.get(line.change_type, " ")
                parts.append(f"{prefix}{line.content}")
            parts.append("```")
        parts.append("")

    # Neighborhood context
    if neighborhood and neighborhood.snippets:
        parts.append("## Neighborhood Context (callers, tests)\n")
        for snippet in neighborhood.snippets[:10]:
            parts.append(f"**{snippet.source}:** `{snippet.file_path}`")
            parts.append("```")
            parts.append(snippet.content)
            parts.append("```")
            parts.append("")

    return "\n".join(parts)


# ─── TOOL 11g [REVIEW · AI]: codewalk_review_next_batch ─────────────
@mcp.tool()
def codewalk_review_next_batch(session_id: str) -> str:
    """Get the next batch of files to review from an active review session.

    Call this repeatedly after codewalk_run_review until all batches are reviewed.
    Each call returns context for the next 3-5 related files.

    Args:
        session_id: Session ID from codewalk_run_review.

    Returns:
        Next batch context, or completion message when all batches are done.
    """
    import json as _json

    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available."

    from src.codewalk.review.session_store import load_session, _session_dir
    from src.codewalk.review.static_analysis import run_static_analysis
    from src.codewalk.review.rubric_loader import Rubrics
    from src.codewalk.review.diff_parser import get_diff, get_parsed_diff

    session = load_session(Path(repo_path), session_id)
    if session is None:
        return f"❌ Session `{session_id}` not found."

    folder = session.folder_name or session.session_id
    session_dir = _session_dir(Path(repo_path), folder)
    batch_state_path = session_dir / "batch_state.json"

    if not batch_state_path.exists():
        return f"❌ No batch state found for session `{session_id}`. Was it started with codewalk_run_review?"

    batch_state = _json.loads(batch_state_path.read_text(encoding="utf-8"))
    current_idx = batch_state["current_batch_index"] + 1
    total = batch_state["total_batches"]

    if current_idx >= total:
        return (
            f"✅ **All {total} batches reviewed** ({batch_state['total_files']} files).\n\n"
            f"All your findings have been saved. Now call:\n"
            f"`codewalk_get_review_summary('{session_id}')` to get the full findings summary,\n"
            f"then produce a final verdict for the user."
        )

    # Get the batch file paths
    batch_paths = batch_state["batch_queue"][current_idx]

    # Rebuild DiffFiles for this batch
    repo = Path(repo_path)
    raw_diff = get_diff(
        target_branch=batch_state.get("target_branch"),
        commit=batch_state.get("commit"),
        staged=batch_state.get("staged", False),
        repo_path=str(repo),
    )
    all_diff_files = get_parsed_diff(raw_diff)
    batch_diff_files = [df for df in all_diff_files if df.file_path in set(batch_paths)]

    # Run static_result (cached)
    static_result = run_static_analysis(
        repo_path=repo,
        target_branch=batch_state.get("target_branch"),
        commit=batch_state.get("commit"),
        staged=batch_state.get("staged", False),
        use_cache=True,
    )

    # Rebuild rubrics from stored state
    rubrics = Rubrics(
        core=batch_state.get("rubric_core", ""),
        language=batch_state.get("rubric_language", {}),
        framework=batch_state.get("rubric_framework", ""),
        fallback=batch_state.get("rubric_fallback", ""),
    )

    stack_header = batch_state.get("stack_header", "")

    # Build context for this batch
    batch_context = _build_batch_context_for_host(
        repo, batch_diff_files, static_result, stack_header, rubrics,
    )

    # Update batch index
    batch_state["current_batch_index"] = current_idx
    batch_state_path.write_text(_json.dumps(batch_state, indent=2), encoding="utf-8")

    remaining = total - current_idx - 1
    lines = [
        f"## Batch {current_idx + 1}/{total}\n",
        batch_context,
        "",
        "---",
        "**After reviewing this batch:**",
        f"1. Call `codewalk_submit_batch_findings('{session_id}', findings=[...])` with your findings",
    ]
    if remaining > 0:
        lines.append(f"2. Call `codewalk_review_next_batch('{session_id}')` for the next batch")
        lines.append(f"**{remaining} batches remaining.**")
    else:
        lines.append(f"2. Call `codewalk_get_review_summary('{session_id}')` to produce the final verdict")
        lines.append("**This is the last batch.**")

    return "\n".join(lines)


# ─── TOOL 11h [REVIEW · AI]: codewalk_submit_batch_findings ─────────
@mcp.tool()
def codewalk_submit_batch_findings(session_id: str, findings: list[dict]) -> str:
    """Save findings from the current batch to persistent storage.

    Call this after reviewing each batch. Findings are appended to llm_findings.json
    so your context window stays clean between batches. A human-readable
    llm_findings.md companion is regenerated at the same time.

    Each finding should be a dict with:
      - file_path: str (required)
      - line_number: int | null
      - severity: "blocker" | "error" | "suggestion"
      - title: str (short description)
      - explanation: str (why this is an issue)
      - current_code: str | null (the problematic code)
      - recommended_code: str | null (the fix)
      - blocking: bool (true if this blocks merge)

    Args:
        session_id: Session ID from codewalk_run_review.
        findings: List of finding dicts from this batch.

    Returns:
        Confirmation with running total.
    """
    import json as _json

    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available."

    from src.codewalk.review.session_store import load_session, _session_dir

    session = load_session(Path(repo_path), session_id)
    if session is None:
        return f"❌ Session `{session_id}` not found."

    folder = session.folder_name or session.session_id
    session_dir = _session_dir(Path(repo_path), folder)
    llm_findings_path = session_dir / "llm_findings.json"

    if not llm_findings_path.exists():
        return f"❌ No llm_findings.json found. Was this session started with codewalk_run_review?"

    # Load existing findings
    existing = _json.loads(llm_findings_path.read_text(encoding="utf-8"))

    # Tag each finding with batch number
    batch_state_path = session_dir / "batch_state.json"
    batch_num = 0
    if batch_state_path.exists():
        bs = _json.loads(batch_state_path.read_text(encoding="utf-8"))
        batch_num = bs.get("current_batch_index", 0) + 1

    for f in findings:
        f["batch"] = batch_num
        f.setdefault("source", "llm")
        f.setdefault("id", _finding_id_from_dict(f))
        f.setdefault("user_verdict", None)

    existing.extend(findings)

    # Write back
    llm_findings_path.write_text(
        _json.dumps(existing, indent=2), encoding="utf-8"
    )

    # Keep human-readable Markdown companion in sync
    from src.codewalk.review.renderers.markdown import render_findings_markdown

    md_path = session_dir / "llm_findings.md"
    md_path.write_text(
        render_findings_markdown(
            existing,
            title="LLM Findings",
            source_label="review LLM",
        ),
        encoding="utf-8",
    )

    return (
        f"✅ Saved {len(findings)} findings from batch {batch_num}. "
        f"Running total: {len(existing)} LLM findings."
    )


# ─── TOOL 11i [REVIEW · AI]: codewalk_get_review_summary ────────────
@mcp.tool()
def codewalk_get_review_summary(session_id: str) -> str:
    """Get the full review summary combining Layer 0 + all LLM findings.

    Call this after all batches are reviewed and findings submitted.
    Returns a structured summary for you to present the final verdict to the user.

    If this session was created by codewalk_re_review, any findings whose IDs
    match previously rejected findings are hidden from the summary.

    Args:
        session_id: Session ID from codewalk_run_review or codewalk_re_review.

    Returns:
        Combined findings summary with Layer 0 architectural warnings + all LLM
        findings across batches, ready for final verdict.
    """
    import json as _json

    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available."

    from src.codewalk.review.session_store import load_session, _session_dir

    session = load_session(Path(repo_path), session_id)
    if session is None:
        return f"❌ Session `{session_id}` not found."

    folder = session.folder_name or session.session_id
    session_dir = _session_dir(Path(repo_path), folder)

    # Load both finding files
    l0_path = session_dir / "static_findings.json"
    llm_path = session_dir / "llm_findings.json"

    static_findings = []
    if l0_path.exists():
        static_findings = _json.loads(l0_path.read_text(encoding="utf-8"))

    llm_findings = []
    if llm_path.exists():
        llm_findings = _json.loads(llm_path.read_text(encoding="utf-8"))

    # Load re-review state and filter out previously rejected findings
    batch_state_path = session_dir / "batch_state.json"
    batch_state = {}
    if batch_state_path.exists():
        batch_state = _json.loads(batch_state_path.read_text(encoding="utf-8"))

    rejected_ids = set(batch_state.get("rejected_ids", []))
    filtered_count = 0
    if rejected_ids:
        before = len(llm_findings)
        llm_findings = [f for f in llm_findings if f.get("id") not in rejected_ids]
        filtered_count = before - len(llm_findings)

    # Build summary
    parts: list[str] = []
    parts.append(f"# Review Summary — Session `{session_id}`\n")

    if batch_state:
        parts.append(f"- **{batch_state['total_files']} files** reviewed in **{batch_state['total_batches']} batches**")

    # Stats
    total = len(static_findings) + len(llm_findings)
    blocking = sum(1 for f in llm_findings if f.get("blocking"))
    blockers = sum(1 for f in llm_findings if f.get("severity") == "blocker")
    errors = sum(1 for f in llm_findings if f.get("severity") == "error")
    suggestions = sum(1 for f in llm_findings if f.get("severity") == "suggestion")

    parts.append(f"- **{total} total findings** ({len(static_findings)} architectural + {len(llm_findings)} from review)")
    if filtered_count:
        parts.append(f"- **{filtered_count} previously rejected finding(s)** hidden in this re-review summary")
    if blocking:
        parts.append(f"- **{blocking} BLOCKING** (must fix before merge)")
    parts.append(f"- Blocker: {blockers} | Error: {errors} | Suggestion: {suggestions}")
    parts.append("")

    # Layer 0 findings (architectural)
    if static_findings:
        parts.append("## Architectural Warnings (deterministic)\n")
        for i, f in enumerate(static_findings, 1):
            parts.append(f"{i}. **{f.get('title', 'Untitled')}**")
            parts.append(f"   - {f.get('explanation', '')}")
            parts.append("")

    # LLM findings grouped by severity
    if llm_findings:
        parts.append("## Review Findings\n")

        for severity_label in ["blocker", "error", "suggestion"]:
            group = [f for f in llm_findings if f.get("severity") == severity_label]
            if not group:
                continue
            parts.append(f"### {severity_label.upper()} ({len(group)})\n")
            for i, f in enumerate(group, 1):
                blocking_tag = " 🚫 BLOCKING" if f.get("blocking") else ""
                parts.append(f"{i}. **{f.get('title', 'Untitled')}**{blocking_tag}")
                parts.append(f"   - File: `{f.get('file_path', '?')}` L{f.get('line_number', '?')}")
                parts.append(f"   - {f.get('explanation', '')}")
                if f.get("recommended_code"):
                    parts.append(f"   - Fix: `{f['recommended_code'][:100]}`")
                parts.append("")

    # Verdict guidance
    parts.append("---")
    parts.append("**Produce your final verdict:**")
    parts.append("- If any BLOCKING findings → `request_changes`")
    parts.append("- If only warnings/suggestions → `approve` with comments")
    parts.append("")
    parts.append("**Present findings to user, then for each finding ask: accept or reject?**")
    parts.append(f"**Edit `llm_findings.json` to set `user_verdict` to 'accepted' or 'rejected' for each finding, then call `codewalk_apply_and_verify_fix('{session_id}')`.**")

    return "\n".join(parts)


# ─── TOOL 11b [MAINT · AI]: codewalk_get_review_details ────────────
@mcp.tool()
def codewalk_get_review_details(session_id: str) -> str:
    """Retrieve a persisted review context package by session_id.

    Use this after codewalk_run_review to resume inspection of a previous review
    context package.

    Args:
        session_id: The session_id returned internally by codewalk_run_review.

    Returns:
        Markdown context package, or an error if the session is not found.
    """
    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available."

    session = load_session(Path(repo_path), session_id)
    if session is None:
        return f"❌ Review session `{session_id}` not found."

    if session.context_package:
        return session.context_package.to_markdown()

    return f"Session `{session_id}` has no context package yet. Status: {session.status.value}"


# ─── TOOL 11d [MAINT · AI]: codewalk_review_file ──────────────────
@mcp.tool()
def codewalk_review_file(
    file_path: str,
    target_branch: str | None = None,
    staged: bool = False,
) -> str:
    """Review a single file and return full context for the host LLM to review.

    Returns the file's content, diff hunks, risk annotations, neighborhood
    context (callers, tests), and rubrics. The host LLM (you) performs the
    actual review using this enriched context.

    No external LLM is called — this tool only returns raw context.

    Args:
        file_path: Relative path to the file to review (e.g. "src/auth/login.py").
        target_branch: Diff working tree against this branch. Shows committed +
            staged + unstaged + untracked changes. If None, reviews local changes.
        staged: If True, review ONLY staged changes for this file (narrow mode).

    Returns:
        Markdown context package for the host LLM to review.
    """
    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available. Run codewalk_analyze_codebase first."

    # Stack context required for correct rubric loading
    if err := _require_stack(f"codewalk_review_file('{file_path}')"):
        return err

    try:
        state.ensure_initialized()
    except Exception:
        pass

    try:
        from src.codewalk.review.engine import _build_common_context
        from src.codewalk.review.rubric_loader import build_rubrics
        from src.codewalk.review.stack_detect import (
            _load_cached,
            format_stack_context_header,
            get_rubric_names_from_stack,
        )
        from src.codewalk.review.utils import get_full_file_tree
        from src.codewalk.codewalk_config import load_codewalk_yaml

        repo = Path(repo_path)
        codewalk_yaml = load_codewalk_yaml(str(repo))

        # Run deterministic analysis for this file only
        static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree = _build_common_context(
            repo, target_branch, None, staged, codewalk_yaml,
        )

        # Stack detection: read persistent file if it exists, else empty (no prompt for single-file)
        cached_stack = _load_cached(repo)
        stack = {k: v for k, v in cached_stack.items() if not k.startswith("_")} if cached_stack else {}
        stack_header = format_stack_context_header(stack)
        rubric_names = get_rubric_names_from_stack(stack)
        rubrics = build_rubrics(repo, {file_path}, detected_rubric_names=rubric_names)

        # Filter to the requested file
        target_diff_files = [df for df in diff_files if df.file_path == file_path]

        # If file has no diff, still provide its content for review
        if not target_diff_files:
            full_path = repo / file_path
            if not full_path.exists():
                return f"❌ File not found: `{file_path}`"

            content = full_path.read_text(encoding="utf-8")

            parts = [f"# Single File Review: `{file_path}`\n"]
            parts.append("No diff found for this file — reviewing current content.\n")
            if stack_header:
                parts.append(stack_header)
            parts.append("## Review Rubric\n")
            if rubrics.core:
                parts.append(rubrics.core)
            parts.append(f"\n## File Content\n```\n{content[:15000]}\n```\n")
            parts.append("---\n**Review this file for bugs, security issues, and style problems.**")
            return "\n".join(parts)

        # Build context using the same helper as batched review
        batch_context = _build_batch_context_for_host(
            repo, target_diff_files, static_result, stack_header, rubrics,
        )

        lines = [f"# Single File Review: `{file_path}`\n"]

        # Static findings for this file
        file_static = [f for f in static_findings if f.file_path == file_path]
        if file_static:
            lines.append(f"**{len(file_static)} architectural warning(s)**\n")

        lines.append(batch_context)
        lines.append("\n---")
        lines.append("**Review this file for bugs, security issues, logic errors, and style.**")
        lines.append("Report findings with: file_path, line_number, severity (blocker/error/suggestion), title, explanation, current_code, recommended_code.")

        return "\n".join(lines)

    except Exception as e:
        _log(f"[codewalk_review_file] error: {e}")
        return f"❌ Review failed for `{file_path}`: {e}"


# ─── TOOL 11k [REVIEW · AI]: codewalk_apply_and_verify_fix ───────────
@mcp.tool()
def codewalk_apply_and_verify_fix(session_id: str = "") -> str:
    """Apply all accepted fixes and verify with static analysis + tests in one step.

    Reads verdicts from llm_findings.json (user edits user_verdict field directly),
    applies each accepted fix (current_code → recommended_code),
    runs static analysis and tests on modified files, then persists verification
    status back to the session JSON.

    No approval token needed — the verdict IS the approval.

    If no session_id is provided, uses the most recent session on the current branch.

    Args:
        session_id: Optional session ID. If empty, uses the latest session.

    Returns:
        Combined markdown: applied/failed/skipped fixes + static analysis
        + test results + per-finding verification status.
    """
    import os
    import json as _json
    from src.codewalk.review.fix_applier import apply_fix_to_file
    from src.codewalk.review.session_store import load_session, _session_dir
    from src.codewalk.review.finding_store import find_last_review
    from src.codewalk.review.utils import get_current_branch
    from src.codewalk.tools.static_analysis import run_static_analysis as _run_sa
    from src.codewalk.tools.test_runner import run_tests as _run_tests
    from src.codewalk.review.renderers.markdown import render_findings_markdown

    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available."

    # ── 1. Resolve session ──────────────────────────────────────────
    if session_id:
        session = load_session(Path(repo_path), session_id)
        if session is None:
            return f"❌ Session `{session_id}` not found."
        folder = session.folder_name or session.session_id
    else:
        branch = get_current_branch(Path(repo_path))
        last_store = find_last_review(Path(repo_path), branch)
        if not last_store:
            return "❌ No previous review session found on this branch."
        session = load_session(Path(repo_path), last_store.review_id)
        if session is None:
            return "❌ Could not load the latest review session."
        folder = session.folder_name or session.session_id

    session_dir = _session_dir(Path(repo_path), folder)
    llm_path = session_dir / "llm_findings.json"
    if not llm_path.exists():
        return f"❌ No llm_findings.json found for session `{session_id or folder}`."

    findings = _json.loads(llm_path.read_text(encoding="utf-8"))
    if not findings:
        return "❌ No findings in this session."

    # ── 2. Filter accepted findings with code ───────────────────────
    to_apply = [
        (i, f) for i, f in enumerate(findings)
        if f.get("user_verdict") == "accepted"
        and f.get("recommended_code")
        and f.get("current_code")
        and f.get("file_path")
    ]

    if not to_apply:
        accepted_count = sum(1 for f in findings if f.get("user_verdict") == "accepted")
        if accepted_count == 0:
            return "⚠️ No findings marked as accepted. Edit llm_findings.json and set user_verdict to 'accepted' first."
        return f"⚠️ {accepted_count} finding(s) accepted but none have both current_code and recommended_code to apply."

    # ── 3. Apply each fix ───────────────────────────────────────────
    applied: list[tuple[int, dict]] = []
    applied_labels: list[str] = []
    failed_labels: list[str] = []
    modified_files: list[str] = []

    for idx, finding in to_apply:
        file_path = finding["file_path"]
        old_code = finding["current_code"]
        new_code = finding["recommended_code"]

        # Path safety check
        full_path = os.path.join(repo_path, file_path)
        resolved_repo = os.path.realpath(repo_path)
        resolved_target = os.path.realpath(full_path)
        if not resolved_target.startswith(resolved_repo + os.sep) and resolved_target != resolved_repo:
            failed_labels.append(f"#{idx} {file_path}: path traversal blocked")
            continue

        result = apply_fix_to_file(repo_path, file_path, old_code, new_code)
        if result["ok"]:
            applied.append((idx, finding))
            applied_labels.append(f"#{idx} {file_path}: {finding.get('title', 'fix applied')}")
            if file_path not in modified_files:
                modified_files.append(file_path)
        else:
            failed_labels.append(f"#{idx} {file_path}: {result.get('error', 'unknown error')}")

    # ── 4. Run verification on modified files ───────────────────────
    sa_issues = []
    test_result = None

    if modified_files:
        sa_issues = _run_sa(repo_path, modified_files)
        test_result = _run_tests(repo_path, modified_files)

    # ── 5. Determine verification status ────────────────────────────
    # Static analysis severity values: "critical", "warning", "info" (standard),
    # "high", "medium", "low" (bandit). Flag anything above informational.
    has_sa_errors = any(
        getattr(i, "severity", "").lower() in ("critical", "high", "warning")
        for i in sa_issues
    )
    tests_passed = test_result is None or test_result.ok
    verification_passed = not has_sa_errors and tests_passed

    # ── 6. Persist status back to findings JSON ─────────────────────
    sa_summary = f"{len(sa_issues)} issue(s)" if sa_issues else "clean"
    test_summary = "pass" if tests_passed else "fail"

    for idx, _finding in applied:
        findings[idx]["status"] = "fixed" if verification_passed else "still_present"
        findings[idx]["verifier_notes"] = f"SA: {sa_summary}, Tests: {test_summary}"

    llm_path.write_text(_json.dumps(findings, indent=2), encoding="utf-8")
    (session_dir / "llm_findings.md").write_text(
        render_findings_markdown(findings, title="LLM Findings", source_label="review LLM"),
        encoding="utf-8",
    )

    # ── 7. Build combined report ────────────────────────────────────
    lines = [f"## Apply & Verify — Session `{session_id or folder}`\n"]

    # Applied / failed
    lines.append(f"**{len(applied)} applied**, {len(failed_labels)} failed\n")
    if applied_labels:
        lines.append("### ✅ Applied:")
        for a in applied_labels:
            lines.append(f"- {a}")
    if failed_labels:
        lines.append("\n### ❌ Failed:")
        for f in failed_labels:
            lines.append(f"- {f}")

    # Static analysis
    lines.append(f"\n### Static Analysis — {len(sa_issues)} issue(s)")
    if sa_issues:
        for issue in sa_issues[:25]:
            loc = f"{issue.file_path}:{issue.line}" if issue.line else issue.file_path
            lines.append(f"- **{issue.severity}** {loc} — {issue.message} ({issue.tool})")
    else:
        lines.append("✅ No static-analysis issues found.")

    # Tests
    if test_result is not None:
        test_status = "✅ PASSED" if test_result.ok else "❌ FAILED"
        lines.append(f"\n### Test Results — {test_status}")
        cmd_str = " ".join(test_result.command) if test_result.command else "(none)"
        lines.append(f"Command: `{cmd_str}`")
        if test_result.stdout:
            lines.extend(["", "```", test_result.stdout[-2000:], "```"])
        if test_result.stderr:
            lines.extend(["", "stderr:", "```", test_result.stderr[-1000:], "```"])
        if test_result.error:
            lines.append(f"\nError: {test_result.error}")

    # Verification summary
    if verification_passed:
        lines.append(f"\n### ✅ Verification Passed")
        lines.append(f"All {len(applied)} applied fix(es) verified successfully.")
    else:
        lines.append(f"\n### ⚠️ Verification Issues Detected")
        lines.append(f"Fixes applied but verification found issues. Review the static analysis and test results above.")

    return "\n".join(lines)


# ─── TOOL 11c [MAINT · AI]: codewalk_get_stack_info ────────────────
@mcp.tool()
def codewalk_get_stack_info() -> str:
    """Return the repository file tree and a structured prompt for stack detection.

    Scans the repo's folder structure (config files, package manifests, folder
    names) and returns a prompt asking the host LLM to produce a JSON describing
    the project's tech stack.

    No git diff, no static analysis — just the file tree. Stack detection is
    about the project's overall architecture, not about what changed in a PR.

    Returns:
        A prompt containing the file tree + instructions for the host LLM to
        produce a JSON and call `codewalk_save_stack_context(your_json)`.
    """
    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available. Run codewalk_analyze_codebase first."

    try:
        from src.codewalk.review.utils import get_full_file_tree
        from src.codewalk.review.stack_detect import _STACK_DETECT_PROMPT, AVAILABLE_RUBRICS

        repo = Path(repo_path)
        codewalk_cfg = load_codewalk_yaml(str(repo))
        file_tree = get_full_file_tree(repo, codewalk_config=codewalk_cfg)

        tree_text = "\n".join(f"- {p}" for p in file_tree[:200])
        if len(file_tree) > 200:
            tree_text += f"\n- ... and {len(file_tree) - 200} more files"

        prompt = _STACK_DETECT_PROMPT.format(
            available_rubrics=", ".join(sorted(AVAILABLE_RUBRICS)),
            file_tree=tree_text,
            changed_files="(not applicable — detecting overall project stack)",
        )

        return (
            f"{prompt}\n\n"
            f"---\n\n"
            f"**After analyzing the above**, respond with the JSON object and call "
            f"`codewalk_save_stack_context(your_json)` to save it."
        )
    except Exception as e:
        _log(f"[codewalk_get_stack_info] error: {e}")
        return f"❌ Stack info gathering failed: {e}"


# ─── TOOL 11j [REVIEW · AI]: codewalk_save_stack_context ────────────
@mcp.tool()
def codewalk_save_stack_context(stack_json: str) -> str:
    """Save the project's stack context (architecture, state management, etc.).

    Call this when codewalk_run_review returns a 'Stack Detection Required' prompt,
    OR when the user wants to update their project's stack info (e.g. after adding
    a new framework).

    You (the host LLM) analyze the file tree and respond with a JSON object
    describing the project's stack. This tool saves it to
    .codewalk/stack_context.json — a persistent file that survives across commits.
    All future reviews use it automatically until explicitly refreshed.

    To refresh: call codewalk_run_review(refresh_stack=True) or call this tool
    again with updated JSON.

    Args:
        stack_json: JSON string with the detected stack. Must include:
            {
              "languages": ["dart"],
              "frameworks": ["dart_flutter"],
              "architecture": "clean architecture with BLoC pattern",
              "state_management": "BLoC + Freezed",
              "data_layer": "Dio + Retrofit, Hive for local",
              "testing": "widget tests + bloc_test",
              "api_style": "REST with Dio"
            }

    Returns:
        Confirmation message. Call codewalk_run_review again to start the review.
    """
    try:
        repo_path = state.get_repo_path()
    except Exception:
        return "❌ No repository path available. Run codewalk_analyze_codebase first."

    from src.codewalk.review.stack_detect import (
        _parse_llm_response,
        _save_cache,
        AVAILABLE_RUBRICS,
    )

    repo = Path(repo_path)

    # Parse the JSON (handles markdown fences too)
    parsed = _parse_llm_response(stack_json)
    if not parsed:
        return (
            "❌ Invalid JSON. Respond with a valid JSON object:\n"
            '{"languages": [...], "frameworks": [...], "architecture": "...", ...}'
        )

    # Validate framework/language names against available rubrics
    parsed["frameworks"] = [
        f for f in parsed.get("frameworks", [])
        if f in AVAILABLE_RUBRICS
    ]
    parsed["languages"] = [
        l for l in parsed.get("languages", [])
        if l in AVAILABLE_RUBRICS
    ]

    # Save to .codewalk/stack_context.json (persistent, survives across commits)
    _save_cache(repo, parsed)

    arch = parsed.get("architecture", "")
    langs = ", ".join(parsed.get("languages", []))
    frameworks = ", ".join(parsed.get("frameworks", []))

    return (
        f"✅ Stack context saved to `.codewalk/stack_context.json`.\n"
        f"- Languages: {langs}\n"
        f"- Frameworks: {frameworks}\n"
        f"- Architecture: {arch}\n\n"
        f"This persists across commits — no need to re-detect on every review.\n"
        f"Now call `codewalk_run_review(target_branch='...')` to start the review."
    )


# ─── TOOL 12 [MAINT · user+AI]: codewalk_load_guidelines ────────────
@mcp.tool()
def codewalk_load_guidelines(docs_path: str | None = None) -> str:
    """Load project docs/standards for use in code reviews.

    Indexes .md/.txt/.rst/.pdf documents from the given directory into the
    project's doc collection. Reviews will automatically include:
      - an explicit `code_guidelines` file configured in codewalk.yaml, or
      - any file named `code_guidelines.md`/`.txt`/`.rst` inside docs_path.
    Call this once per project or after the docs change.

    Args:
        docs_path: Path to directory containing doc/guideline files.

    Returns:
        Success message with count of embedded chunks, or error message.
    """
    import os

    path = docs_path
    if not path:
        return "❌ No path provided. Pass docs_path."

    if not os.path.isdir(path):
        return f"❌ Directory not found: {path}"

    repo_path = state.get_repo_path()
    if not repo_path:
        return "❌ No repository path available. Run codewalk_analyze_codebase first."

    from src.codewalk.doc_knowledge.doc_store import DocStore

    col = f"{state.get_collection_name()}_docs"
    doc_store = DocStore(persist_dir=state.chroma_path(), collection_name=col)
    doc_store.create_collection()
    doc_store.clear()
    count = doc_store.index_docs(path)

    return (
        f"✅ Indexed {count} doc chunks from {path}\n"
        f"`code_guidelines` (configured in codewalk.yaml or found in this folder) will be used automatically in codewalk_run_review."
    )


# ══════════════════════════════════════════════════════════════════════
#  EXECUTION TOOLS — static analysis and test runners
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 13b [EXEC · user+AI]: codewalk_run_static_analysis ─────────
@mcp.tool()
def codewalk_run_static_analysis(file_paths: list[str]) -> str:
    """Run language-aware static analyzers (linters, type checkers, security) on files.

    Detects the language from file extensions and runs the appropriate tools:
      - Python: ruff, mypy, bandit
      - JS/TS: eslint
      - Go: go vet
      - Rust: cargo check
      - Java: mvn compile (best-effort)

    Tools that are not installed are skipped gracefully. Configure custom commands
    in codewalk.yaml under tools.static_analysis.<language>.

    Args:
        file_paths: List of relative file paths to analyze.

    Returns:
        Markdown summary of findings, or a message if no analyzers matched.
    """
    from src.codewalk.tools.static_analysis import run_static_analysis

    try:
        state.ensure_initialized()
    except Exception:
        pass

    repo_path = state.get_repo_path()
    issues = run_static_analysis(repo_path, file_paths)

    if not issues:
        return f"✅ No static-analysis issues found in {len(file_paths)} file(s)."

    lines = [
        f"## Static Analysis Findings ({len(issues)} issue(s))",
        "",
    ]
    for issue in issues:
        loc = f"{issue.file_path}:{issue.line}" if issue.line else issue.file_path
        lines.append(
            f"- **[{issue.severity.upper()}]** `{loc}` — {issue.message} "
            f"(rule: {issue.rule}, tool: {issue.tool})"
        )

    return "\n".join(lines)


# ─── TOOL 13c [EXEC · user+AI]: codewalk_run_tests ───────────────────
@mcp.tool()
def codewalk_run_tests(file_paths: list[str] | None = None) -> str:
    """Run the project's test suite (language-aware auto-detection).

    Detects the test command from repo files and file extensions:
      - Python: pytest
      - JS/TS: npm test
      - Go: go test ./...
      - Rust: cargo test
      - Java: mvn test / gradle test

    Configure a custom command in codewalk.yaml under tools.test_command.<language>.

    Args:
        file_paths: Optional list of changed files (used for language detection).

    Returns:
        Test command output and pass/fail status.
    """
    from src.codewalk.tools.test_runner import run_tests

    try:
        state.ensure_initialized()
    except Exception:
        pass

    repo_path = state.get_repo_path()
    result = run_tests(repo_path, file_paths or [])

    status = "✅ PASSED" if result.ok else "❌ FAILED"
    lines = [
        f"## Test Results — {status}",
        f"Command: `{result.command}`",
        "",
        "### stdout",
        "```",
        result.stdout[-2000:] if result.stdout else "(empty)",
        "```",
    ]
    if result.stderr:
        lines.extend([
            "",
            "### stderr",
            "```",
            result.stderr[-2000:],
            "```",
        ])
    if result.error:
        lines.extend(["", f"**Error:** {result.error}"])

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
#  VOICE TOOL — natural language interface to all Codewalk tools
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 14 (COMMENTED OUT) codewalk_start_voice ────────────────────
# Launches voice companion in a separate Terminal.app window (macOS).
# Superseded by codewalk_voice_ask which runs inside Copilot directly.
# Kept for reference / potential CLI use outside MCP.
#
# @mcp.tool()
# def codewalk_start_voice(backend: str = "direct") -> str:
#     """Launch the Codewalk Voice Companion in a new Terminal window.
#
#     Opens Terminal.app on macOS and runs the voice companion — press Enter
#     to speak, Codewalk listens via mic, routes via Ollama, speaks the answer.
#
#     Use this when user says "start voice", "open voice companion", or
#     "I want to talk to Codewalk".
#
#     Requires: Microphone access.
#
#     Args:
#         backend: "direct" (default, fastest) or "mcp" (MCP stdio protocol).
#     """
#     import subprocess
#     import sys
#     import os
#     import shlex
#
#     python = sys.executable
#     repo_path = _mcp_repo_path()
#     package_root = __name__.rsplit(".", 2)[0]
#     companion_module = f"{package_root}.voice.companion"
#     server_cwd = os.getcwd()
#
#     cmd = (
#         f"cd {shlex.quote(server_cwd)} && "
#         f"{shlex.quote(python)} -m {companion_module} --backend {backend}"
#     )
#
#     cmd_escaped = cmd.replace('\\', '\\\\').replace('"', '\\"')
#     script = f'tell application "Terminal"\n    activate\n    do script "{cmd_escaped}"\nend tell'
#
#     try:
#         subprocess.run(["osascript", "-e", script], check=True)
#         return f"✅ Voice Companion launched in Terminal.\n\n  Repo: {repo_path}\n  Backend: {backend}"
#     except FileNotFoundError:
#         return f"❌ osascript not found — this tool only works on macOS.\n\nRun manually:\n```\n{cmd}\n```"
#     except subprocess.CalledProcessError as e:
#         return f"❌ Failed to launch Terminal: {e}\n\nRun manually:\n```\n{cmd}\n```"


# ─── TOOL 14 [VOICE · user]: codewalk_voice_ask ──────────────────────
@mcp.tool()
def codewalk_voice_ask() -> str:
    """Record from mic and transcribe — then YOU (Copilot) pick the right tool.

    Records until silence (max 30s), transcribes via local Whisper.
    Returns the transcript so Copilot can route to the correct codewalk tool.

    AFTER calling this tool:
    1. Read the transcript in the result
    2. Call the appropriate codewalk tool based on what the user said
    3. Call codewalk_speak(text) with a concise spoken summary of the answer

    Use when user says "voice ask", "listen to me", "voice question",
    or "let me speak".

    Requires: Microphone access
    """

    def _play_beep(sound_name: str):
        """Play a system beep sound. Falls back silently on non-macOS."""
        sounds = {
            "tink": "/System/Library/Sounds/Tink.aiff",
            "pop": "/System/Library/Sounds/Pop.aiff",
        }
        path = sounds.get(sound_name)
        if path and os.path.exists(path):
            subprocess.run(["afplay", path], check=False)

    # ── 0. Stop any playing audio + beep to signal "start talking" ──
    stop_speaking()
    _play_beep("tink")

    # ── 1. Record from mic ──────────────────────────────────────────
    _log("[codewalk_voice_ask] Recording from mic...")
    try:
        audio = record_audio()
    except Exception as e:
        return f"❌ Mic recording failed: {e}\n\nCheck microphone permissions in System Settings → Privacy → Microphone."

    if len(audio) == 0:
        return "❌ No audio captured. Make sure your microphone is working."

    # ── Beep to signal "recording stopped" ──
    _play_beep("pop")

    # ── 2. Transcribe (faster-whisper, local) ───────────────────────
    _log("[codewalk_voice_ask] Transcribing...")
    transcript = transcribe(audio)
    if not transcript.strip():
        return "❌ Couldn't understand the audio. Try speaking louder or closer to the mic."

    _log(f'[codewalk_voice_ask] Transcript: "{transcript}"')

    # ── Check for "stop" command ───────────────────────────────────
    stop_words = {"stop", "stop talking", "shut up", "be quiet", "enough"}
    if transcript.strip().lower() in stop_words:
        stop_speaking()
        return "🔇 Stopped playback."

    # ── 3. Beep to signal "got it, processing..." ──────────────────
    _play_beep("tink")

    return (
        f'🎤 **Transcript:** "{transcript}"\n\n'
        f"Route and respond:\n"
        f"1. Pick the correct tool using these rules:\n"
        f"   - User names a specific module → `codewalk_get_module_info(name)`\n"
        f"   - User asks what a specific function/method does → `codewalk_explain_function(name)`\n"
        f"   - User asks what a specific class/component/type does → `codewalk_explain_class(name)`\n"
        f"   - User asks how something works (concept/flow) → `codewalk_search_codebase(query)`\n"
        f"   - User asks for an overview or summary → `codewalk_get_overview()`\n"
        f"   - User asks about risk or what breaks → `codewalk_get_blast_radius_map(target)`\n"
        f"   - User asks about dependencies or execution flow → `codewalk_get_execution_flow()`\n"
        f"   - User asks where to start reading → `codewalk_get_reading_order()`\n"
        f"   - User asks to review changes → call codewalk_run_review() (pass target_branch='...' only if comparing to a branch) and review the returned raw context with the host LLM\n"
        f"   - User says 'apply that fix' / 'make that change' → `codewalk_approve_action(proposed_action=<fix>)` first, wait for yes\n"
        f"   - User asks about docs/guides/runbooks → `codewalk_ask_docs(question)`\n"
        f"   - User asks to index/load documents → `codewalk_index_docs(path)`\n"
        f"   - DEFAULT: if user names something that could be a module → `codewalk_get_module_info`, otherwise → `codewalk_search_codebase`\n"
        f"2. Show the FULL tool result as text in the chat — same detail as a typed question.\n"
        f"3. Then call `codewalk_speak()` with a 2-4 sentence plain-English spoken summary.\n"
        f"⚠️ NEVER skip step 2 or 3. NEVER pass the full tool output to speak — summarize it."
    )


# ─── TOOL 15 [VOICE · user]: codewalk_speak ──────────────────────────
@mcp.tool()
def codewalk_speak(text: str) -> str:
    """Speak text aloud via TTS (edge-tts, en-US-AriaNeural).

    Call this after getting a tool result to speak a concise summary to the user.
    Keep text to 2-4 sentences — conversational, no markdown, no file paths.

    Args:
        text: Plain English text to speak. No markdown, bullets, or code.
    """
    _log(f"[codewalk_speak] Speaking: {text[:80]}...")
    try:
        speak(text)
        return f"🔊 Spoken: {text}"
    except Exception as e:
        return f"❌ TTS failed: {e}"


# ─── TOOL 16 [QUERY · user+AI]: codewalk_get_architecture_health ─────
@mcp.tool()
def codewalk_get_architecture_health() -> str:
    """Architecture health report: bottlenecks, key files, circular dependencies.

    Returns:
    - Declared architecture (from .codewalk/stack_context.json)
    - Graph stats (files, import edges, DAG status)
    - Bottleneck files (betweenness centrality — most import paths pass through these)
    - Most important files (PageRank — transitively depended on by the most code)
    - Circular dependencies with suggested fixes (which imports to remove)

    Use when asked about code health, architecture quality, refactoring
    priorities, circular imports, or "what should I fix first?"

    Requires .codewalk/stack_context.json for full output.
    """
    if err := _require_index():
        return err
    if err := _require_stack("codewalk_get_architecture_health()"):
        return err

    runtime = state.get_graph_runtime()
    sections = []

    # ── Declared architecture from stack context ──
    from src.codewalk.review.stack_detect import _load_cached
    repo = Path(state.get_repo_path())
    cached_stack = _load_cached(repo) or {}
    if cached_stack.get("architecture"):
        sections.append(
            f"## Declared Architecture\n\n"
            f"- **Architecture:** {cached_stack['architecture']}\n"
            + (f"- **State management:** {cached_stack['state_management']}\n" if cached_stack.get("state_management") else "")
            + (f"- **Data layer:** {cached_stack['data_layer']}\n" if cached_stack.get("data_layer") else "")
        )

    # ── Graph stats ──
    stats = runtime.get_graph_stats()
    sections.append(
        f"## Architecture Health Report\n\n"
        f"**Files:** {stats['file_graph']['vertices']} | "
        f"**Import edges:** {stats['file_graph']['edges']} | "
        f"**DAG:** {'Yes' if stats['file_graph']['is_dag'] else 'No (has cycles)'}\n"
        f"**Modules:** {stats['module_graph']['vertices']} | "
        f"**Module edges:** {stats['module_graph']['edges']}"
    )

    # ── Bottleneck files (betweenness centrality) ──
    # High betweenness = many shortest paths pass through this file.
    # If it breaks, it disrupts the most connections.
    centrality = runtime.centrality(top_n=10)
    if centrality["betweenness"]:
        between_lines = []
        for item in centrality["betweenness"]:
            if item["score"] > 0:
                name = item["file"].rsplit("/", 1)[-1]
                between_lines.append(f"  - {name} (score: {item['score']})")
        if between_lines:
            sections.append(
                "### Bottleneck Files (betweenness centrality)\n"
                "These files sit on the most import paths. Changes here ripple widely.\n"
                + "\n".join(between_lines)
            )

    # ── Most important files (PageRank) ──
    # High PageRank = imported by other important files (transitive).
    if centrality["pagerank"]:
        pagerank_lines = []
        for item in centrality["pagerank"][:10]:
            name = item["file"].rsplit("/", 1)[-1]
            pagerank_lines.append(f"  - {name} (score: {item['score']})")
        sections.append(
            "### Most Important Files (PageRank)\n"
            "Transitively depended on by the most code.\n"
            + "\n".join(pagerank_lines)
        )

    # ── Circular dependencies ──
    # Cycle groups + minimum edges to break (feedback arc set).
    cycles = runtime.detect_cycles()
    if cycles["has_cycles"]:
        cycle_lines = [f"### Circular Dependencies ({len(cycles['cycle_groups'])} cycle groups)"]
        for index, group in enumerate(cycles["cycle_groups"], 1):
            names = [file.rsplit("/", 1)[-1] for file in group]
            cycle_lines.append(f"  Cycle {index}: {' ↔ '.join(names)}")
            for file in group:
                cycle_lines.append(f"    - {file}")
        if cycles["edges_to_break"]:
            cycle_lines.append("\n**Suggested Fixes (minimum imports to remove):")
            for source, target in cycles["edges_to_break"]:
                cycle_lines.append(f"  - Remove: {source.rsplit('/', 1)[-1]} → {target.rsplit('/', 1)[-1]}")
        sections.append("\n".join(cycle_lines))
    else:
        sections.append("### Circular Dependencies\nNone — clean DAG.")

    return "\n\n".join(sections)

# ─── TOOL 17 [QUERY · user+AI]: codewalk_call_chain ──────────────────
@mcp.tool()
def codewalk_call_chain(source: str, target: str) -> str:
    """Trace the import chain between two files.

    Shows the shortest path of imports from source to target.
    Useful for understanding how changes propagate through the codebase.

    Args:
        source: Source file name or path (e.g. "pipeline.py" or "src/codewalk/pipeline.py")
        target: Target file name or path (e.g. "config.py" or "src/codewalk/config.py")
    """
    if err := _require_index():
        return err

    runtime = state.get_graph_runtime()
    chain = runtime.shortest_path(source, target)

    if not chain:
        return (
            f"No import path found from '{source}' to '{target}'.\n"
            f"They may be in separate parts of the codebase with no dependency connection."
        )
    
    short_names = [f.rsplit("/", 1)[-1] for f in chain]
    chain_str = " → ".join(short_names)

    lines = [
        f"## Import Chain: {short_names[0]} → {short_names[-1]}",
        f"**Hops:** {len(chain) - 1}",
        f"**Path:** {chain_str}",
        "",
        "### Full paths:",
    ]

    for i, file_path in enumerate(chain):
        marker = "📍" if i == 0 or i == len(chain) - 1 else "  →"
        lines.append(f"  {marker} {file_path}")

    return "\n".join(lines)


# ─── TOOL 18 [DOCS]: codewalk_index_docs ─────────────────────────────
@mcp.tool()
def codewalk_index_docs(docs_path: str) -> str:
    """Index a folder of documents (.md, .pdf, .txt) for semantic search.

    Parses all supported documents, splits them into chunks by section/page,
    embeds them, and stores in a separate ChromaDB collection.

    Args:
        docs_path: Absolute path to the documents folder.
                   Example: "/Users/you/team-docs"
    """
    store = state.get_doc_store()

    result = store.index_docs(docs_path)

    if result["chunks_stored"] == 0:
        return (
            f"No supported documents found in: {docs_path}\n"
            f"Supported formats: .md, .pdf, .txt"
        )
    
    return (
        f"## Docs Indexed Successfully\n"
        f"**Documents:** {result['docs_found']}\n"
        f"**Chunks:** {result['chunks_stored']}\n"
        f"\nYou can now use `codewalk_search_docs(query)` to search these documents."
    )

# ─── TOOL 19 [DOCS]: codewalk_search_docs ────────────────────────────
@mcp.tool()
def codewalk_search_docs(query: str, n_results: int = 5) -> str:
    """Search indexed documents for content matching a query.

    Performs a single semantic search and returns the most relevant document
    chunks with source citations. Use this after codewalk_index_docs.

    For broad doc questions, call this tool 1-3 times with different phrasings
    and synthesize the merged chunks.

    Args:
        query: What to search for (e.g. "deployment process", "API authentication").
        n_results: Number of results to return (default 5).
    """
    store = state.get_doc_store()

    if store.chunk_count() == 0:
        return "No documents indexed yet. Run codewalk_index_docs(path) first."
    
    results = store.search(query, n_results=n_results)
    if not results:
        return f"No relevant documents found for: '{query}'"
    
    lines = [f"## Document Search: '{query}'\n"]

    for index, result in enumerate(results):
        meta = result["metadata"]
        distance = result.get("distance")

        score_str = f" (distance: {distance:.3f})" if distance is not None else ""

        lines.append(f"### Result {index + 1}{score_str}")
        lines.append(f"**Source:** {meta.get('doc_path', '?')} > {meta.get('section', '?')}")
        if meta.get("page"):
            lines.append(f"**Page:** {meta['page']}")

        lines.append(f"```\n{result['text'][:15000]}\n```\n")
    
    return "\n".join(lines)

# ─── TOOL 20 [DOCS]: codewalk_ask_docs ───────────────────────────────
@mcp.tool()
def codewalk_ask_docs(question: str, n_results: int = 5) -> str:
    """Ask a question and get grounded context from indexed documents.

    Performs a single semantic search, formats the relevant chunks with source
    citations, and returns a prompt for the host LLM to answer from.

    For broad doc questions, call this tool 1-3 times with different phrasings,
    merge the returned chunks, and synthesize one answer.

    Args:
        question: The question to answer from the docs.
        n_results: Number of document chunks to retrieve (default 5).
    """
    store = state.get_doc_store()

    if store.chunk_count() == 0:
        return "No documents indexed yet. Run codewalk_index_docs(path) first."

    results = store.search(question, n_results=n_results)

    if not results:
        return f"No relevant documents found for: '{question}'"

    # Build context — same format DOC_ASK_PROMPT expects
    context_parts = []
    for r in results:
        meta = r["metadata"]
        source = f"{meta.get('doc_path', '?')} > {meta.get('section', '?')}"
        context_parts.append(f"--- {source} ---\n{r['text']}")

    context = "\n\n".join(context_parts)

    # Return the full prompt — Copilot reads this and answers
    return DOC_ASK_PROMPT.format(context=context, question=question)


# ─── TOOL 21 [HITL · AI]: codewalk_approve_action ─────
@mcp.tool()
def codewalk_approve_action(proposed_action: str) -> str:
    """Request user approval before taking any action that modifies code, files, or external systems.

    Called by the IDE agent over MCP. Each host (Cursor, VS Code Copilot, Claude Code, etc.)
    has its own approve/reject UI — present this message there, or in chat if the host has
    no tool-approval surface. Wait for user approval before codewalk_apply_fix.

    Returns a message containing a single-use approval_token (required by apply_fix after approval).

    Args:
        proposed_action: What you intend to do. Be specific — include the exact
                         file paths, diff, PR title, or command that will run.
                         e.g. "Apply this fix to auth/login.py:\n+  if not rate_limit..."
    """
    global _pending_approval_token
    _pending_approval_token = secrets.token_hex(8)
    return (
        f"⏸ ACTION REQUIRES YOUR APPROVAL\n\n"
        f"{proposed_action}\n\n"
        f"Approval token: `{_pending_approval_token}`\n"
        f"(Pass this to codewalk_apply_fix only after the user says yes.)\n\n"
        f"Present this to the user for approval (use your host's approve/reject UI when available).\n"
        f"Reply **yes** to proceed or **no** to cancel if confirming in chat.\n"
        f"If rejected — skip this fix and move to the next issue."
    )

# ─── TOOL 21b [HITL · AI]: codewalk_apply_fix ─────
@mcp.tool()
def codewalk_apply_fix(
    file_path: str,
    old_code: str,
    new_code: str,
    approval_token: str,
    session_id: str | None = None,
    finding_index: int | None = None,
) -> str:
    """Apply a code fix by replacing old_code with new_code in the file.

    This tool ACTUALLY EDITS FILES ON DISK. Requires approval_token from the
    immediately prior codewalk_approve_action after the user said yes in chat.

    You can apply a fix in two ways:
      1. Pass explicit file_path, old_code, and new_code.
      2. Pass session_id and finding_index to load the fix from the session's
         llm_findings.json (old_code and new_code are taken from the finding).

    Performs an exact text replacement: searches for old_code in the file and
    replaces it with new_code. Fails if old_code is not found or appears multiple
    times (to prevent accidental replacements).

    Args:
        file_path: Relative path to the file (e.g. "src/auth/login.py")
        old_code: The EXACT code to search for (must match file content precisely)
        new_code: The replacement code
        approval_token: Token from codewalk_approve_action (single-use)
        session_id: Optional. Session ID from codewalk_run_review's context package.
        finding_index: Optional. Index of the finding in the session's llm_findings.json.

    Returns:
        Success message with the applied change, or error message if replacement failed.
    """
    import os
    from src.codewalk.review.fix_applier import apply_fix_to_file
    from src.codewalk.review.utils import load_finding_by_session_and_index

    global _pending_approval_token
    if not _pending_approval_token or approval_token != _pending_approval_token:
        return (
            "❌ Fix not applied — missing or invalid approval.\n\n"
            "For each issue: call codewalk_approve_action → show the user → wait for yes → "
            "then codewalk_apply_fix with the approval_token from that response."
        )
    _pending_approval_token = None

    repo_path = state.get_repo_path()

    # If session_id + finding_index are provided, load the finding from llm_findings.json.
    if session_id is not None and finding_index is not None:
        finding = load_finding_by_session_and_index(Path(repo_path), session_id, finding_index)
        if finding is None:
            return f"❌ Finding not found: session `{session_id}`, index {finding_index}."
        file_path = finding.get("file_path", file_path)
        old_code = finding.get("current_code") or old_code
        new_code = finding.get("recommended_code") or new_code

    full_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

    # Prevent path traversal outside the repo (defense in depth)
    resolved_repo = os.path.realpath(repo_path)
    resolved_target = os.path.realpath(full_path)
    if not resolved_target.startswith(resolved_repo + os.sep) and resolved_target != resolved_repo:
        return f"❌ Invalid file path: {file_path} is outside the repository."

    result = apply_fix_to_file(repo_path, file_path, old_code, new_code)
    if not result["ok"]:
        return f"❌ {result['error']}"

    # Show a mini diff of the change
    old_lines = old_code.splitlines()
    new_lines = new_code.splitlines()
    max_lines = max(len(old_lines), len(new_lines))
    diff_lines = []
    for index in range(max_lines):
        old_line = old_lines[index] if index < len(old_lines) else ""
        new_line = new_lines[index] if index < len(new_lines) else ""
        if old_line != new_line:
            if old_line:
                diff_lines.append(f"- {old_line}")
            if new_line:
                diff_lines.append(f"+ {new_line}")

    diff_preview = "\n".join(diff_lines[:20])
    if len(diff_lines) > 20:
        diff_preview += "\n... (truncated)"

    validation_note = ""
    if result.get("validation"):
        validation_note = f"\nValidation: {result['validation']['message']}"
    if result.get("formatter"):
        fmt = result["formatter"]
        if not fmt["ok"]:
            validation_note += f"\nFormatter warning: {fmt.get('stderr', '')}"

    return (
        f"✅ Fix applied to {file_path}{validation_note}\n\n"
        f"---\n"
        f"{diff_preview}\n"
        f"---\n\n"
        f"Moving to the next issue..."
    )


# ─── TOOL 22 [CLOUD · AI]: codewalk_pull_index ─────
@mcp.tool()
def codewalk_pull_index(force: bool = False) -> str:
    """Download the latest cloud index, replacing local .codewalk/.
    Requires: CODEWALK_SERVER_URL, CODEWALK_REPO_NAME, CODEWALK_REPO_TOKEN in env.

    Args:
        force: If True, download the cloud index even when the local index is
               newer than the cloud index. Use this only when you intentionally
               want to roll back to the cloud version.
    """
    server_url = os.getenv("CODEWALK_SERVER_URL")
    repo_name  = os.getenv("CODEWALK_REPO_NAME")
    repo_token = os.getenv("CODEWALK_REPO_TOKEN")
    if not all([server_url, repo_name, repo_token]):
        return "Not configured for cloud. Set CODEWALK_SERVER_URL, CODEWALK_REPO_NAME, CODEWALK_REPO_TOKEN."

    # Check if already up to date before downloading
    local_meta = _local_manifest_path()
    if local_meta.exists() and not force:
        try:
            remote = requests.get(
                f"{server_url}/indexes/{repo_name}/manifest",
                headers={"X-Repo-Token": repo_token},
                timeout=5,
            ).json()
            local_version = json.loads(local_meta.read_text()).get("index_version", 0)
            remote_version = remote.get("index_version", 0)
            if remote_version <= local_version:
                if remote_version < local_version and not force:
                    return (
                        f"⚠️ Local index (v{local_version}) is ahead of cloud (v{remote_version}).\n\n"
                        "If you really want to overwrite your local index with the older cloud version, "
                        "call codewalk_pull_index with force=True."
                    )
                if remote_version == local_version:
                    return f"Already up to date (v{local_version})."
        except Exception:
            pass  # If manifest check fails, proceed with download

    codewalk_dir = _download_index(server_url, repo_name, repo_token)

    # Clear stale in-memory state so the next query tool reloads from the
    # freshly downloaded index. _repo_path is discovered from cwd by
    # state.ensure_initialized() / _resolve_repo_path() when needed.
    _reset_state()

    local_meta_after = codewalk_dir / "manifest.json"
    new_version = ""
    if local_meta_after.exists():
        try:
            new_version = f" (v{json.loads(local_meta_after.read_text()).get('index_version', '?')})"
        except Exception:
            pass
    return f"Index updated{new_version}. Using latest version now.\nExtracted to: {codewalk_dir}"

# ─── TOOL 23 [CLOUD · AI]: codewalk_index_status ─────
@mcp.tool()
def codewalk_index_status() -> str:
    """Show local vs remote index freshness (version, commit SHA, indexed_at, file count)."""
    server_url = os.getenv("CODEWALK_SERVER_URL")
    repo_name  = os.getenv("CODEWALK_REPO_NAME")
    repo_token = os.getenv("CODEWALK_REPO_TOKEN")

    local_meta = _local_manifest_path()
    local = json.loads(local_meta.read_text()) if local_meta.exists() else {}

    try:
        remote = requests.get(
            f"{server_url}/indexes/{repo_name}/manifest",
            headers={"X-Repo-Token": repo_token}, timeout=5,
        ).json()
    except Exception as e:
        remote = {"error": str(e)}

    local_v  = local.get("index_version", "?")
    remote_v = remote.get("index_version", "?")

    lines = [
        f"Local:  v{local_v}  {local.get('commit_sha','none')[:7]}  {local.get('indexed_at','?')}  {local.get('file_count','?')} files",
        f"Cloud:  v{remote_v}  {remote.get('commit_sha','none')[:7]}  {remote.get('indexed_at','?')}  {remote.get('file_count','?')} files",
    ]

    if isinstance(local_v, int) and isinstance(remote_v, int):
        diff = remote_v - local_v
        if diff == 0:
            lines.append("✅ Up to date")
        elif diff > 0:
            lines.append(f"⚡ {diff} version(s) behind — run codewalk_pull_index to update")
        else:
            lines.append("⚠️  Local is ahead of cloud (unusual)")
    elif local.get("commit_sha") == remote.get("commit_sha"):
        lines.append("✅ Up to date")
    else:
        lines.append("⚡ Remote is newer — run codewalk_pull_index to update")

    return "\n".join(lines)


# ─── Commit mismatch helper (session-cached warning) ─────────────────
_commit_mismatch_warned: bool = False

def _check_commit_mismatch(local_repo_path: str, manifest: dict) -> str:
    """Compare local git HEAD with the commit SHA recorded in the downloaded index.

    Returns a warning string on mismatch, empty string if they match or
    cannot be determined. Warning is shown only once per session.
    """
    global _commit_mismatch_warned
    if _commit_mismatch_warned:
        return ""

    index_sha = manifest.get("commit_sha", "")
    if not index_sha:
        return ""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=local_repo_path,
            capture_output=True, text=True, check=True,
        )
        local_sha = result.stdout.strip()
    except Exception:
        return ""  # Can't determine local commit — silent

    if local_sha == index_sha:
        return ""

    _commit_mismatch_warned = True
    return (
        f"⚠️  Commit mismatch detected:\n"
        f"  Local repository: {local_sha[:7]}\n"
        f"  Downloaded index: {index_sha[:7]}\n"
        f"  Results may be inaccurate.\n"
        f"  Run 'git pull' to sync, then codewalk_pull_index to refresh."
    )


# ─── TOOL 24 [CLOUD · user]: codewalk_connect_repo ─────
@mcp.tool()
def codewalk_connect_repo(repo_name: str, repo_token: str, force: bool = False) -> str:
    """Connect a local repository to its cloud index.

    Automatically:
    1. Detects git root of current working directory
    2. Validates repo_name matches origin remote
    3. Downloads latest index from cloud (replaces local .codewalk/ — no merge)
    4. Extracts fresh .codewalk/ into git root
    5. Warns if local commit differs from index commit

    Args:
        repo_name:  Repository in 'owner/repo' format.
        repo_token: Per-repo download token (cw_repo_xxxxxxxx).
        force:      If True, replace the local index even if it is newer than the
                    cloud index. Use with caution — this rolls back to the cloud version.
    """
    server_url = os.getenv("CODEWALK_SERVER_URL", "")
    if not server_url:
        return "❌ CODEWALK_SERVER_URL not set. Add it to your mcp.json environment."

    # Step 1: Detect git root
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "❌ Not a git repository. Run this tool from inside your cloned repo."

    # Step 2: Validate repo_name matches origin remote
    try:
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=git_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Extract owner/repo from HTTPS or SSH remote URL
        repo_slug = (
            remote_url.replace("https://github.com/", "")
                      .replace("git@github.com:", "")
                      .removesuffix(".git")
                      .strip("/")
        )
        if repo_slug != repo_name:
            return (
                f"❌ Repo name mismatch.\n"
                f"  Provided:  {repo_name}\n"
                f"  Origin:    {repo_slug}\n"
                f"  Use the exact owner/repo from your GitHub URL."
            )
    except Exception:
        return "❌ Could not read git remote 'origin'. Ensure the repo has an origin remote."

    # Step 3: Verify cloud manifest exists (proves the repo is indexed)
    try:
        manifest_resp = requests.get(
            f"{server_url}/indexes/{repo_name}/manifest",
            headers={"X-Repo-Token": repo_token},
            timeout=10,
        )
        if manifest_resp.status_code == 404:
            return (
                f"❌ No index found for {repo_name}.\n"
                f"  Has it been pushed to GitHub? Wait for the webhook to finish indexing."
            )
        if manifest_resp.status_code == 403:
            return "❌ Invalid token. Check CODEWALK_REPO_TOKEN."
        manifest_resp.raise_for_status()
        manifest = manifest_resp.json()
    except requests.RequestException as exc:
        return f"❌ Could not reach cloud server: {exc}"

    # Guard against overwriting a newer local index with an older cloud one.
    local_manifest_path = Path(git_root) / ".codewalk" / "manifest.json"
    if local_manifest_path.exists() and not force:
        try:
            local_version = json.loads(local_manifest_path.read_text()).get("index_version", 0)
            remote_version = manifest.get("index_version", 0)
            if local_version > remote_version:
                return (
                    f"⚠️ Local index (v{local_version}) is ahead of cloud (v{remote_version}).\n\n"
                    "If you really want to overwrite your local index with the older cloud version, "
                    "call codewalk_connect_repo with force=True."
                )
        except Exception:
            pass

    # Step 4+5: Download to temp file FIRST, then replace local index.
    # This avoids losing the local index if the download fails.
    import tempfile

    try:
        dl_resp = requests.get(
            f"{server_url}/indexes/{repo_name}",
            headers={"X-Repo-Token": repo_token},
            stream=True, timeout=300,
        )
        dl_resp.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            for chunk in dl_resp.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tarball = Path(tmp.name)
    except Exception as exc:
        return f"❌ Download failed: {exc}"

    try:
        # Only delete the old index AFTER a successful download
        codewalk_dir = Path(git_root) / ".codewalk"
        if codewalk_dir.exists():
            shutil.rmtree(codewalk_dir)
        subprocess.run(
            ["tar", "-xzf", str(tarball), "-C", git_root],
            check=True,
        )
    except Exception as exc:
        return f"❌ Extraction failed: {exc}"
    finally:
        tarball.unlink(missing_ok=True)

    # Point in-memory state at the connected repo and clear stale caches so the
    # next tool call reloads from the freshly downloaded index.
    state.set_repo_path(git_root)
    _reset_state()

    # Write CODEWALK_REPO_NAME into mcp.json (NOT the token — tokens must
    # stay in environment variables or a user-scoped secret store to avoid
    # accidental commits to version control).
    mcp_json_path = Path(git_root) / "mcp.json"
    mcp_hint = ""
    if mcp_json_path.exists():
        try:
            mcp_cfg = json.loads(mcp_json_path.read_text())
            env = mcp_cfg.setdefault("env", {})
            env["CODEWALK_REPO_NAME"] = repo_name
            mcp_json_path.write_text(json.dumps(mcp_cfg, indent=2))
            mcp_hint = (
                "\n  mcp.json updated with CODEWALK_REPO_NAME."
                "\n  Set CODEWALK_REPO_TOKEN as an environment variable "
                "(do NOT commit tokens to mcp.json)."
            )
        except Exception:
            mcp_hint = "\n  ⚠️  Could not update mcp.json."
    else:
        mcp_hint = (
            "\n  Add to mcp.json env:\n"
            f'    "CODEWALK_REPO_NAME": "{repo_name}"\n'
            "  Set CODEWALK_REPO_TOKEN as an environment variable."
        )

    index_version = manifest.get("index_version", "?")
    index_sha = manifest.get("commit_sha", "?")[:7]

    # Step 8: Commit mismatch check
    mismatch_warning = _check_commit_mismatch(git_root, manifest)

    lines = [
        f"✅ Connected! Index v{index_version} downloaded.",
        f"  Index commit: {index_sha}",
        mcp_hint,
    ]
    if mismatch_warning:
        lines.append(mismatch_warning)
    lines.append("  Run codewalk_index_status anytime to check for updates.")

    return "\n".join(lines)


# ─── TOOL 25 [CLOUD · AI]: codewalk_check_version ─────
@mcp.tool()
def codewalk_check_version() -> str:
    """Check if a newer version of Codewalk is available on the cloud server."""
    from src.codewalk.staleness import check_version_message

    return check_version_message()


def _kill_process_on_port(port: int) -> None:
    """Kill Codewalk/Next.js processes on the given port (safe — checks command name)."""
    if sys.platform == "darwin":
        try:
            # Get PIDs and their commands to avoid killing unrelated services
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, check=False,
            )
            pids = result.stdout.strip()
            if pids:
                for pid in pids.splitlines():
                    pid = pid.strip()
                    if not pid:
                        continue
                    # Check if it's a node/next process before killing
                    cmd_result = subprocess.run(
                        ["ps", "-p", pid, "-o", "command="],
                        capture_output=True, text=True, check=False,
                    )
                    cmd = cmd_result.stdout.strip().lower()
                    if any(name in cmd for name in ("node", "next", "npm", "codewalk")):
                        subprocess.run(["kill", "-9", pid], capture_output=True, check=False)
        except Exception:
            pass
    else:
        try:
            subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, check=False)
        except Exception:
            pass


# ─── TOOL 26 [VISUALIZATION · user+AI]: codewalk_show_knowledge_graph ──
@mcp.tool()
def codewalk_show_knowledge_graph(repo_path: str = "", port: int = 3000) -> str:
    """Open the interactive knowledge graph dashboard for the current repo.

    This tool kills any existing Codewalk frontend on the given port and starts
    the pre-built production frontend (npm start). It returns a URL the user
    can open in a browser.

    The production server boots in seconds because the Next.js bundle is already
    built. If the bundle is missing, the tool auto-builds it first. If the bundle
    is stale, run `npm run build` in the `frontend/` directory manually before
    calling this tool.

    Args:
        repo_path: Absolute path to the repo to visualize. If empty, discovers
                   the repo from the current MCP workspace via codewalk.yaml.
        port: Port where the Codewalk frontend should run (default 3000).
    """
    import time
    import urllib.request
    import urllib.error

    # 1. Resolve repo path
    target = (repo_path or _mcp_repo_path()).strip()
    try:
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=target,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        target = git_root
    except Exception:
        target = str(Path(target).resolve())

    # 2. Check for knowledge graph JSON
    kg_path = Path(target) / ".codewalk" / "knowledge-graph.json"
    if not kg_path.exists():
        return (
            f"❌ No knowledge graph found at {kg_path}\n\n"
            f"Run `codewalk analyze` locally, or `@codewalk analyze this codebase` "
            f"in MCP/cloud if cloud indexing is set up. Then try again."
        )

    # 3. Find Codewalk install root (where frontend/ lives)
    env_root = os.getenv("CODEWALK_INSTALL_ROOT", "").strip()
    install_root = Path(env_root).resolve() if env_root else Path(__file__).resolve().parents[3]
    if not (install_root / "frontend" / "package.json").exists():
        return (
            f"❌ Could not find Codewalk frontend installation.\n"
            f"Set CODEWALK_INSTALL_ROOT to the directory containing frontend/package.json."
        )

    frontend_dir = install_root / "frontend"
    encoded_repo = requests.utils.quote(target, safe="")
    api_url = f"http://localhost:{port}/api/knowledge-graph?repoPath={encoded_repo}"
    ui_url = f"http://localhost:{port}/knowledge-graph?repoPath={encoded_repo}"

    # 4. Ensure dependencies are installed
    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        return (
            f"❌ Codewalk frontend dependencies not installed.\n"
            f"Run: cd {frontend_dir} && npm install\n"
            f"Then call this tool again."
        )

    # 5. Kill any existing frontend on this port.
    _kill_process_on_port(port)

    # 6. Ensure a production build exists. Auto-build if the bundle is missing
    # so the tool works even when .next was cleared by a dev restart.
    next_dir = frontend_dir / ".next"
    if not next_dir.exists():
        try:
            build_result = subprocess.run(
                ["npm", "run", "build"],
                cwd=str(frontend_dir),
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            return f"❌ Failed to build Codewalk frontend: {exc}\nRun manually: cd {frontend_dir} && npm run build && npm start"
        if build_result.returncode != 0:
            return (
                f"❌ Codewalk frontend build failed.\n"
                f"stdout:\n{build_result.stdout}\n"
                f"stderr:\n{build_result.stderr}\n"
                f"Fix the build errors, then try again."
            )

    # 7. Start the production server in the background.
    # npm start serves the pre-built .next bundle, so it boots in seconds
    # instead of compiling on-demand like npm run dev.
    try:
        subprocess.Popen(
            ["npm", "start"],
            cwd=str(frontend_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return f"❌ Failed to start Codewalk frontend: {exc}\nRun manually: cd {frontend_dir} && npm start"

    # 7. Wait up to ~30s for the server to respond
    for _ in range(60):
        try:
            urllib.request.urlopen(api_url, timeout=1)
            return (
                f"✅ Codewalk frontend restarted on port {port}. Knowledge graph ready.\n"
                f"Open: {ui_url}"
            )
        except urllib.error.URLError:
            time.sleep(0.5)

    return (
        f"⏳ Codewalk frontend is starting on port {port}.\n"
        f"Open this URL in a few seconds: {ui_url}"
    )


# ─── Staleness wrappers + shared tool map (voice_ask, backends.py) ──
from src.codewalk.staleness import install_staleness_wrappers

install_staleness_wrappers(mcp._tool_manager)

# Refresh repo state from cwd before every MCP tool call so workspace switches
# are picked up without restarting the server.
for _tool in mcp._tool_manager.list_tools():
    _tool.fn = refresh_state(_tool.fn)

_TOOL_MAP = {tool.name: tool.fn for tool in mcp._tool_manager.list_tools()}

if __name__ == "__main__":
    mcp.run(transport="stdio")
