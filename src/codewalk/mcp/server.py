"""Codewalk MCP server — 27 tools for codebase onboarding, search, review, voice, and cloud index management.

Tool categories:
  SETUP  (1):           Analyze repo — scans, filters, chunks, embeds in one call.
  QUERY  (2-8, 16-17):  Search code, explain functions, blast radius, reading order, execution flow, architecture health.
  MAINT  (9-13):        Incremental reindex, refresh analysis, review diff/file, load guidelines.
  REVIEW (11, 11b):     Review git diff, self-critique review (reflection pass).
  VOICE  (14-15):       Mic record+transcribe (Copilot routes), TTS speak.
  DOCS   (18-20):       Index, search, and ask questions against team docs.
  HITL   (21):          Human-in-the-loop approval gate before any code/file action.
  CLOUD  (22-25):       Pull index, check index status, connect repo one-click, check Codewalk version.
"""

import inspect
import logging
import os
import json
import secrets
import shutil
import subprocess
from pathlib import Path
import requests

from mcp.server.fastmcp import FastMCP

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

# Single-use HITL token from last codewalk_approve_action (host UI approves; code enforces token)
_pending_approval_token: str | None = None

from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context, retrieve_corrective
from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT

from src.codewalk.pipeline import index_from_paths_parallel, incremental_reindex
from src.codewalk.team_config import load_codewalk_yaml
from src.codewalk.config import settings
from src.codewalk.review.guidelines_loader import get_guidelines_store
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
from src.codewalk.review.reflector import REVIEW_CRITIC_PROMPT


# ─── Create the MCP server ──────────────────────────────────────────
mcp = FastMCP(
    name="codewalk",
    instructions=(
        "Codewalk is an AI-powered codebase onboarding tool. "
        "\n\n"
        "## IMPORTANT: ALWAYS USE CODEWALK TOOLS\n"
        "When a Codewalk tool exists for the task, you MUST call it instead of\n"
        "searching, reading, or analyzing files yourself. Codewalk tools use\n"
        "pre-computed dependency graphs, ChromaDB embeddings, and blast radius\n"
        "analysis that you cannot replicate by reading files.\n"
        "\n"
        "## SETUP WORKFLOW\n"
        "1) codewalk_analyze_codebase — ONE CALL:\n"
        "   • .codewalk/ with chunks on disk → INDEX READY (load only, no re-embed)\n"
        "   • Cloud configured + no index → auto-download from server\n"
        "   • No index + local only → scan (codewalk.yaml excludes), embed on this machine\n"
        "2) Query tools auto-load .codewalk/ on later MCP sessions (no re-analyze needed).\n"
        "\n"
        "## ANSWERING QUESTIONS (after setup)\n"
        "- 'What does X do?' → codewalk_explain_function(X) — line-by-line explanation\n"
        "- 'How does feature Y work?' → codewalk_search_codebase(Y) — returns code chunks for YOU to analyze\n"
        "- 'Give me an overview' → codewalk_get_overview\n"
        "- 'What's in module Z?' → codewalk_get_module_info(Z) — files + functions/classes\n"
        "- 'What breaks if I change X?' → codewalk_get_blast_radius_map(target=X) — "
        "X can be a module name, file name, or empty for top 30 riskiest\n"
        "- 'Where should I start reading?' → codewalk_get_reading_order — returns ALL files\n"
        "- 'Show me the dependency flow' → codewalk_get_execution_flow — "
        "no arg = module-to-module flow, with module_name = file-to-file flow\n"
        "- 'Fix this bug / apply this change' → search + produce fix → codewalk_approve_action FIRST → then apply\n"
        "\n"
        "## MAINTENANCE (after code changes)\n"
        "- codewalk_incremental_reindex — re-embed only changed files (hash-based skip)\n"
        "- codewalk_refresh_analysis — rebuild deps/modules without re-embedding\n"
        "\n"
        "## CODE REVIEW — FULL FLOW (agent-driven via MCP)\n"
        "\n"
        "Codewalk review tools are called by YOU (the IDE agent) over MCP — not by the user directly.\n"
        "Each MCP host has its own approve/reject UX (Cursor cards, Copilot chat, Claude Code prompts, etc.).\n"
        "YOU must present each proposed fix clearly so the host can show it; then wait for the user's\n"
        "approval through that host UI or an explicit yes/no before calling codewalk_apply_fix.\n"
        "\n"
        "Step 1: PARALLEL CONTEXT GATHERING — call ALL THREE simultaneously in one response:\n"
        "        a) codewalk_review_diff       — diff + per-file caller/security/blast context\n"
        "        b) codewalk_get_architecture_health — bottleneck files, circular deps, PageRank\n"
        "        c) codewalk_get_module_info(<affected_module>) — full symbol map of changed module\n"
        "        Wait for all three results before moving to Step 2.\n"
        "\n"
        "Step 2: WRITE YOUR REVIEW merging all three results:\n"
        "        - Use codewalk_review_diff for per-file issues (bugs, security, logic)\n"
        "        - Use codewalk_get_architecture_health to flag if any changed file is a\n"
        "          bottleneck (high centrality) or part of a circular dependency\n"
        "        - Use codewalk_get_module_info to check if changed symbols are widely used\n"
        "        Then ALWAYS call codewalk_reflect_review(initial_review=<your review text>)\n"
        "        Read the returned critic prompt and produce an improved final review.\n"
        "\n"
        "Step 3: For EACH fix you want to apply (iterate ONE BY ONE):\n"
        "        a) Present the fix to the user with:\n"
        "           - File path and line number\n"
        "           - The EXACT old code (what's currently in the file)\n"
        "           - The EXACT new code (the corrected version)\n"
        "        b) Call codewalk_approve_action(proposed_action='<summary of fix>')\n"
        "        c) Display the returned message using YOUR HOST's approve/reject UI if it has one;\n"
        "           otherwise show it in chat and wait for explicit yes/no.\n"
        "        d) If approved: codewalk_apply_fix(..., approval_token=<token from approve output>)\n"
        "        e) If rejected: skip; codewalk_approve_action again for the next issue\n"
        "\n"
        "IMPORTANT: codewalk_apply_fix REQUIRES approval_token — never apply without user approval.\n"
        "\n"
        "- codewalk_load_guidelines(path) — load team coding standards (run once per project)\n"
        "- codewalk_review_file(path) — review a single file (use same parallel pattern above)\n"
        "\n"
        "## ARCHITECTURE ANALYSIS\n"
        "- codewalk_get_architecture_health — bottlenecks, key files, circular dependencies, refactoring priorities\n"
        "- codewalk_call_chain(source, target) — trace the shortest import path between two files\n"
        "\n"
        "## DOCUMENTATION SEARCH\n"
        "- codewalk_index_docs(docs_path) — index a folder of .md/.pdf/.txt docs for semantic search\n"
        "- codewalk_search_docs(query) — search indexed docs, returns raw chunks for browsing\n"
        "- codewalk_ask_docs(question) — search + answer grounded in docs with citations\n"
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
        "Before taking ANY action that modifies code, files, or external systems:\n"
        "  1. Call codewalk_approve_action(proposed_action='<exactly what you will do>')\n"
        "  2. Present the output for the user — use the host approve/reject UI when available.\n"
        "  3. If approved — pass approval_token to codewalk_apply_fix. If rejected — do not apply.\n"
        "This applies to: applying code fixes, creating/editing/deleting files, committing,\n"
        "raising PRs, updating Jira, or ANY irreversible operation.\n"
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
        "codewalk_search_codebase returns RAW code chunks, not a pre-made answer.\n"
        "YOU must generate the answer from the chunks. Follow this flow:\n"
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
        "1. DECOMPOSE: Break the question into 3-any number of independent sub-questions,\n"
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
        "  cloud server. Also runs silently on codewalk_analyze_codebase startup.\n"
        "\n"
        "## ERROR HANDLING\n"
        "If any tool returns a message starting with 'Error:':\n"
        "- 'No index found' → tell user to run codewalk_analyze_codebase first\n"
        "- 'Module not found' → show the available modules from the error message\n"
        "- Never retry the same tool with identical arguments after an error\n"
    ),
)

# For cloud support
def _target_repo_root() -> Path:
    """Git root where .codewalk/ lives — must match REPO_PATH, not MCP server cwd."""
    return Path(state.get_repo_path()).resolve()


def _local_manifest_path() -> Path:
    return _target_repo_root() / ".codewalk" / "manifest.json"


def ensure_local_index():
    """Downloads cloud index into REPO_PATH/.codewalk if missing; shows banner if stale."""
    server_url = os.getenv("CODEWALK_SERVER_URL")
    repo_name  = os.getenv("CODEWALK_REPO_NAME")
    repo_token = os.getenv("CODEWALK_REPO_TOKEN")

    if not all([server_url, repo_name, repo_token]):
        return None

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

    if remote_version > local_version:
        from src.codewalk.api import state
        state.pending_update = (
            f"⚡ NEW INDEX AVAILABLE: v{remote_version} (you have v{local_version})\n"
            f"  Commit: {remote.get('commit_sha','?')[:7]}\n"
            f"  Indexed: {remote.get('indexed_at','?')}\n"
            f"  Run codewalk_pull_index to update.\n"
        )


def _download_index(server_url: str, repo_name: str, repo_token: str):
    repo_root = _target_repo_root()
    print(f"⏳ Downloading index for {repo_name} → {repo_root}/.codewalk/ ...")
    request = requests.get(
        f"{server_url}/indexes/{repo_name}",
        headers={"X-Repo-Token": repo_token},
        stream=True, timeout=120,
    )

    request.raise_for_status()
    tarball = Path("/tmp/codewalk-index.tar.gz")
    with open(tarball, "wb") as file:
        for chunk in request.iter_content(chunk_size=8192):
            file.write(chunk)

    codewalk_dir = repo_root / ".codewalk"
    if codewalk_dir.exists():
        shutil.rmtree(codewalk_dir)
    subprocess.run(["tar", "-xzf", str(tarball), "-C", str(repo_root)], check=True)
    print(f"✅ Index ready ({repo_name})")

# ══════════════════════════════════════════════════════════════════════
#  Index gate — query tools load from disk; analyze builds the index
# ══════════════════════════════════════════════════════════════════════

def _require_index() -> str | None:
    """Return error message if no index on disk, else None (loads index automatically)."""
    if state.ensure_initialized():
        return None
    return state.INDEX_REQUIRED_MCP


# ══════════════════════════════════════════════════════════════════════
#  SETUP TOOLS — user or AI runs these to onboard a codebase
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 1 [SETUP · user+AI]: codewalk_analyze_codebase ────────────
@mcp.tool()
def codewalk_analyze_codebase() -> str:
    """Analyze a codebase structure and prepare for search.

    Call this once to set up a repo. Query tools work automatically after that.

    Flow:
    1. Index on disk (.codewalk/) → load and return INDEX READY
    2. No index → cloud download (if configured) or local embed (codewalk.yaml excludes)

    ⏩ NEXT STEP: use any query tool directly
    """
    _log(f"[codewalk_analyze_codebase] Starting analysis: {settings.repo_path}")

    repo_path = settings.repo_path
    if not repo_path or not os.path.isdir(repo_path):
        return f"❌ Invalid repo path: '{repo_path}' is not a directory."

    state.set_repo_path(repo_path)
    guidelines_path = os.getenv("REVIEW_GUIDELINES_PATH", "")
    docs_path = os.getenv("CODE_DOCS_PATH", "")

    _version_banner = codewalk_check_version() if os.getenv("CODEWALK_SERVER_URL") else ""
    pending_msg = state.pending_update or ""
    if _version_banner and "up to date" not in _version_banner and "Cloud not configured" not in _version_banner:
        pending_msg = (_version_banner + "\n\n" + pending_msg).strip()

    # Cloud: download index if missing; show staleness banner if remote is newer
    ensure_local_index()

    built = False
    embed_result: dict | None = None
    try:
        if state.index_on_disk(repo_path):
            # Full index present — load deps/modules/chroma without re-embedding
            state.load_scoped_analysis()
            if guidelines_path or docs_path:
                state.rebuild_analysis_cache(
                    files=state._files,
                    guidelines_path=guidelines_path,
                    docs_path=docs_path,
                )
        else:
            # Build analysis first (codewalk.yaml excludes); embed locally if chroma empty
            state.rebuild_analysis_cache(
                guidelines_path=guidelines_path,
                docs_path=docs_path,
            )

        state._store = VectorStore(persist_dir=state.chroma_path())
        state._store.create_collection(state.get_collection_name())
        existing = state._store.chunk_count()

        if existing == 0:
            all_paths = [f["file_path"] for f in state._files]
            _log(f"[codewalk_analyze_codebase] INDEX EMPTY → local embedding {len(all_paths)} files")
            if not all_paths:
                return (
                    f"⚠️ No indexable files found after filtering.\n"
                    f"Check codewalk.yaml indexing.exclude or .codewalkignore."
                )
            built = True
            embed_result = index_from_paths_parallel(
                all_paths,
                repo_path,
                state.get_collection_name(),
                persist_dir=state.chroma_path(),
            )
            state._store = VectorStore(persist_dir=state.chroma_path())
            state._store.create_collection(state.get_collection_name())
            existing = state._store.chunk_count()
            if state._graph_store and embed_result.get("embedded_chunks"):
                state._graph_store._populate_chunks(embed_result["embedded_chunks"])
            state._wire_query_state()
    except Exception as e:
        error_msg = str(e)
        if "lock" in error_msg.lower() or "Could not set lock" in error_msg:
            return (
                f"Error: DuckDB lock conflict — another Codewalk process is using the database.\n\n"
                f"{error_msg}\n\n"
                f"Fix: Stop the other process (MCP server, API server, or CLI), then retry."
            )
        raise

    modules = list(state._modules_result["modules"].keys())
    _log(f"[codewalk_analyze_codebase] Modules: {modules} | Index: {existing} chunks")
    if existing > 0:
        # Backfill DuckDB chunks table from ChromaDB if empty
        if state._graph_store:
            state._graph_store.populate_chunks_from_chromadb(state._store)

        # Docs + guidelines already indexed by build_full_analysis
        guidelines_msg = ""
        if guidelines_path:
            gl_store = VectorStore(persist_dir=state.chroma_path())
            gl_store.create_collection("guidelines")
            gl_count = gl_store.chunk_count()
            if gl_count > 0:
                guidelines_msg = f"Guidelines: {gl_count} chunks embedded\n"

        docs_msg = ""
        if docs_path:
            from src.codewalk.doc_knowledge.doc_store import DocStore as _DocStore
            _ds = _DocStore(persist_dir=state.chroma_path(), collection_name=f"{state.get_collection_name()}_docs")
            _ds.create_collection()
            dc = _ds.chunk_count()
            if dc > 0:
                docs_msg = f"Docs: {dc} chunks embedded\n"

        header = f"{pending_msg}\n\n" if pending_msg else ""
        if built and embed_result:
            return (
                header
                + f"Codebase analyzed and indexed successfully.\n"
                + f"Files found: {len(state._files)}\n"
                + f"Files indexed: {embed_result.get('files_scanned', len(state._files))}\n"
                + f"Chunks embedded: {embed_result.get('chunks_embedded', existing)}\n"
                + f"Time: {embed_result.get('total_time', 'N/A')}\n"
                + f"{guidelines_msg}"
                + f"{docs_msg}"
                + f"Modules found: {', '.join(modules)}\n\n"
                + f"✅ Local embedding complete — use query tools directly."
            )
        return (
            header
            + f"Codebase analyzed successfully.\n"
            + f"Files found: {len(state._files)}\n"
            + f"Modules found: {', '.join(modules)}\n"
            + f"Search index: INDEX READY — {existing} chunks available.\n"
            + f"{guidelines_msg}"
            + f"{docs_msg}\n"
            + f"✅ Loaded existing index.\n"
            + f"Ready to answer questions — use query tools directly."
        )

    header = f"{pending_msg}\n\n" if pending_msg else ""
    return (
        header
        + f"⚠️ Indexing produced 0 chunks.\n"
        + f"Files scanned (codewalk.yaml excludes applied): {len(state._files or [])}\n"
        + f"Modules: {', '.join(modules)}\n"
        + f"Local: check embedder (Jina/HF) is installed and REPO_PATH is correct.\n"
        + f"Cloud: rm -rf .codewalk && codewalk_pull_index, then analyze again."
    )

# ══════════════════════════════════════════════════════════════════════
#  QUERY TOOLS — user asks a question, AI picks the right tool
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 2 [QUERY · user+AI]: codewalk_search_codebase ──────────────
@mcp.tool()
def codewalk_search_codebase(query: str) -> str:
    """Search the codebase and return relevant code chunks for analysis.

    Retrieves code chunks by semantic similarity, filters by distance,
    expands via the dependency graph when retrieval is weak, and grades
    chunks by keyword overlap. Returns the raw chunks — YOU (Copilot)
    generate the answer from them.

    If confidence is low or chunks seem irrelevant, rephrase the query
    and call this tool again (max 3 retries).

    For a specific function/class by name, prefer codewalk_explain_function.

    Requires codewalk_analyze_codebase + indexing workflow first.

    Args:
        query: Natural language question, e.g. "how does authentication work",
               "error handling in API routes", "how files get chunked"
    """
    if err := _require_index():
        return err

    _log(f"[codewalk_search_codebase] Query: {query}")
    result = retrieve_corrective(
        query, state._store,
        graph_store=state._graph_store,
    )

    if not result["chunks"]:
        return (
            f"No relevant chunks found for: {query}\n"
            f"Confidence: {result['confidence']:.2f}\n\n"
            f"Try rephrasing with different keywords."
        )

    context = format_context(result["chunks"])
    meta = (
        f"\n\n---\n"
        f"Confidence: {result['confidence']:.2f} | "
        f"Retrieval good: {result['retrieval_good']} | "
        f"Chunks: {result['total_after_filter']}/{result['total_retrieved']}\n"
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

# ─── TOOL 5 [QUERY · user+AI]: codewalk_get_overview ─────────────────
@mcp.tool()
def codewalk_get_overview() -> str:
    """Get the project overview from Codewalk's computed analysis.

    Returns:
    - Tech stack detection results
    - Module list with file counts and languages
    - Module dependency flow (entry points → core modules)
    - Top 30 riskiest files by blast radius with break chains
    """
    if err := _require_index():
        return err

    _log("[codewalk_get_overview] Generating overview...")
    return overview_text(state._repo_path, state._modules_result, state._deps, state._graph_runtime)

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
    and removes chunks for deleted files. Much faster than full re-index.

    Requires: codebase must be indexed at least once via codewalk_analyze_codebase.
    After that, call this tool whenever code changes to keep embeddings in sync.

    Returns a summary showing how many files were skipped, re-indexed,
    or deleted, plus the number of new chunks embedded.

    ⏪ PREVIOUS STEP: codewalk_analyze_codebase (first-time setup)
    ⏩ NEXT STEP: any query tool (search, explain, blast radius, etc.)
    """
    if err := _require_index():
        return err

    repo_path = state.get_repo_path()
    team_config = load_codewalk_yaml(repo_path)

    # Fall back to all indexed files
    paths = list(state._store.get_all_indexed_files())
    if not paths:
        return "❌ No files to reindex. Run codewalk_analyze_codebase first."
    
    result = incremental_reindex(
        paths, repo_path, state.get_collection_name(),
        persist_dir=state.chroma_path(), team_config=team_config,
    )

    guidelines_path = os.getenv("REVIEW_GUIDELINES_PATH", "")
    docs_path = os.getenv("CODE_DOCS_PATH", "")
    state.rebuild_analysis_cache(
        embedded_chunks=result.get("embedded_chunks"),
        guidelines_path=guidelines_path,
        docs_path=docs_path,
        force_reindex_extras=True,
    )

    return (
        f"Incremental reindex complete ({result['total_time']})\n\n"
        f"  Files on disk:   {result['files_on_disk']}\n"
        f"  Skipped (same):  {result['files_skipped']}\n"
        f"  Re-indexed:      {result['files_reindexed']}\n"
        f"  Deleted:         {result['files_deleted']}\n"
        f"  Chunks embedded: {result['chunks_embedded']}\n\n"
        f"Analysis cache refreshed (docs + guidelines re-indexed)."
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


# ─── TOOL 11 [MAINT · user+AI]: codewalk_review_diff ────────────
@mcp.tool()
def codewalk_review_diff(
    staged: bool = False,
    target_branch: str | None = None,
    commit: str | None = None,
) -> str:
    """Review the current git diff for bugs, security vulnerabilities, and logic errors.

    Gathers the diff, enriches it with codebase context (file contents, dependency
    graph, security patterns from the vector index), runs automated pre-checks,
    and returns everything for deep analysis.

    Automated pre-checks:
      - Test coverage: flags source files missing corresponding test updates
      - Blast radius: warns about high-risk files with many dependents

    Args:
        staged: If True, review only staged changes (--staged). Default: all unstaged.
        target_branch: Diff against a branch (e.g. "main" for full PR review).
        commit: Review a specific commit by SHA or ref (e.g. "abc1234", "HEAD", "HEAD~2").
    """
    from src.codewalk.review.reviewer import prepare_review_context

    try:
        state.ensure_initialized()
    except Exception:
        pass  # review works without full indexing — just less context

    ctx = prepare_review_context(
        staged=staged,
        target_branch=target_branch,
        commit=commit,
        store=state._store,
        deps=state._deps,
        repo_path=state.get_repo_path(),
        graph_store=state._graph_store,
    )

    if ctx is None:
        return "No changes to review (empty diff)."

    # ── Build output ──
    output_parts = []
    output_parts.append(
        f"## Code Review Request — {len(ctx.diff_files)} files "
        f"(+{ctx.total_added} / -{ctx.total_removed})\n"
    )

    # Pre-check results
    if ctx.pre_check_issues:
        output_parts.append("### Pre-check Findings")
        for issue in ctx.pre_check_issues:
            output_parts.append(
                f"- 🟡 [{issue.severity.value}] {issue.file_path}: {issue.title}"
            )
        output_parts.append("")

    # Blast radius warnings
    if ctx.blast_radius_warnings:
        output_parts.append("### Blast Radius Warnings")
        for w in ctx.blast_radius_warnings:
            output_parts.append(f"- ⚠️ {w}")
        output_parts.append("")

    # Guidelines
    if ctx.guidelines_context:
        output_parts.append(ctx.guidelines_context)
        output_parts.append("")

    # Team docs (architecture, naming conventions, folder structure)
    if ctx.docs_context:
        output_parts.append(ctx.docs_context)
        output_parts.append("")

    # Architecture patterns detected
    if ctx.architecture_context:
        output_parts.append(ctx.architecture_context)
        output_parts.append("")

    # ── Per-file diff + context ──
    output_parts.append("---\n### Files to Review\n")

    for fc in ctx.file_contexts:
        df = fc.diff_file
        output_parts.append(f"## File: {df.file_path} (+{df.added_lines}/-{df.removed_lines})")

        if fc.file_content:
            output_parts.append(f"<full_file>\n{fc.file_content}\n</full_file>")

        if fc.caller_context:
            output_parts.append(fc.caller_context)

        if fc.security_context:
            output_parts.append(fc.security_context)

        output_parts.append(f"<diff>\n{fc.file_diff_text}\n</diff>\n")

    # ── Review instructions ──
    output_parts.append(
        "---\n"
        "## YOUR TASK\n"
        "Review each file above. For each issue found, report:\n"
        "- Severity: 🔴 CRITICAL / 🟡 WARNING / 🟢 SUGGESTION\n"
        "- File and line number\n"
        "- What's wrong and why it's dangerous\n"
        "- The EXACT old code (the problematic code as it currently exists in the file)\n"
        "- The EXACT new code (the corrected replacement — copy-pasteable)\n"
        "- One sentence explaining what the fix does\n\n"
        "IMPORTANT: When applying fixes, use codewalk_approve_action first, then codewalk_apply_fix\n"
        "with approval_token. apply_fix requires EXACT old_code and EXACT new_code.\n"
        "Make sure your old_code matches the file content exactly, including whitespace.\n\n"
        "Focus on: OWASP top 10 (injection, auth bypass, XSS, SSRF, open redirect), "
        "race conditions, resource leaks, null safety, async gaps (setState after await "
        "without mounted check), unbounded growth, hardcoded secrets, certificate pinning "
        "bypass, SQL injection, path traversal. Be AGGRESSIVE — better to over-flag.\n\n"
        "⏩ NEXT STEPS (if you haven't already called them in parallel):\n"
        "  - codewalk_get_architecture_health — check if changed files are bottlenecks or in cycles\n"
        "  - codewalk_get_module_info(<module>) — check blast radius of changed symbols\n"
        "  Then merge all results and call codewalk_reflect_review(initial_review=<your review>)"
    )

    return "\n".join(output_parts)

# ─── TOOL 11b [MAINT · AI]: codewalk_reflect_review ────────────
@mcp.tool()
def codewalk_reflect_review(
    initial_review: str,
    staged: bool = False,
    target_branch: str | None = None,
    commit: str | None = None,
) -> str:
    """Self-critique an initial code review to catch missed issues and remove false positives.

    Call this immediately after YOU have written a review from codewalk_review_diff.
    Returns the diff + your initial review + critic instructions — YOU then apply the
    critic role and produce an improved final review.

    No LLM is called here. YOU are the LLM — this tool just formats the reflection input.

    ⏩ AFTER producing your improved review:
    - For each fix: codewalk_approve_action → show user → wait for yes →
      codewalk_apply_fix(..., approval_token=<token from approve>)

    Args:
        initial_review: The review you just wrote after calling codewalk_review_diff.
        staged:         Same value you passed to codewalk_review_diff (default: False).
        target_branch:  Same value you passed to codewalk_review_diff (if any).
        commit:         Same value you passed to codewalk_review_diff (if any).
    """
    from src.codewalk.review.reviewer import prepare_review_context

    state.ensure_initialized()

    ctx = prepare_review_context(
        staged=staged,
        target_branch=target_branch,
        commit=commit,
        store=state._store,
        deps=state._deps,
        repo_path=state.get_repo_path(),
        graph_store=state._graph_store,
    )

    if ctx is None:
        return "No diff available to reflect on."

    return (
        f"{REVIEW_CRITIC_PROMPT}\n\n"
        f"DIFF:\n```\n{ctx.diff_text[:10000]}\n```\n\n"
        f"INITIAL REVIEW:\n{initial_review}"
    )


@mcp.tool()
def codewalk_review_file(file_path: str) -> str:
    """Review a single file for bugs, security vulnerabilities, and logic errors.

    Reads the file, enriches it with codebase context (who imports it,
    security patterns from the vector index, team guidelines), and returns
    everything for deep analysis.

    Does NOT require the file to be in git diff — works on any file in the repo.

    Requires: codebase must be indexed first via codewalk_analyze_codebase.

    Args:
        file_path: Path to the file to review (relative to repo root).
    """
    import os
    from src.codewalk.review.reviewer import (
        _get_caller_context, _get_security_context_for_file,
    )
    from src.codewalk.review.models import DiffFile, DiffHunk, ChangedLine
    from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
    from src.codewalk.rag.chain import format_context

    try:
        state.ensure_initialized()
    except Exception:
        pass  # review works without full indexing — just less context

    repo_path = state.get_repo_path()
    full_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

    if not os.path.exists(full_path):
        return f"❌ File '{file_path}' not found."

    try:
        with open(full_path, "r", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return f"❌ Cannot read file: {e}"

    # Build a synthetic DiffFile so we can reuse context helpers
    lines = content.splitlines()
    changed_lines = [
        ChangedLine(line_number=i + 1, content=line, change_type="added")
        for i, line in enumerate(lines)
    ]
    synthetic_diff = DiffFile(
        file_path=file_path,
        language="",
        hunks=[DiffHunk(start_line=1, end_line=len(lines), lines=changed_lines)],
        is_new_file=True,
        added_lines=len(lines),
        removed_lines=0,
    )

    # ── Build context ──
    output_parts = []
    output_parts.append(f"## File Review: {file_path} ({len(lines)} lines)\n")

    # Caller context (who imports this file)
    caller_ctx = _get_caller_context(synthetic_diff, state._deps, state._graph_store)
    if caller_ctx:
        output_parts.append(caller_ctx)
        output_parts.append("")

    # Security patterns from vector store
    if state._store:
        sec_ctx = _get_security_context_for_file(synthetic_diff, state._store)
        if sec_ctx:
            output_parts.append(sec_ctx)
            output_parts.append("")

    # Codebase patterns (similar code elsewhere)
    if state._store:
        result = retrieve_corrective(
            f"code in {file_path}", state._store,
            graph_store=state._graph_store,
        )
        if result["chunks"]:
            output_parts.append("## Similar patterns elsewhere in the codebase")
            output_parts.append(format_context(result["chunks"]))
            output_parts.append("")

    # Guidelines
    guidelines_store = get_guidelines_store(persist_dir=state.chroma_path())
    if guidelines_store:
        gl = search_guidelines(guidelines_store, [synthetic_diff], n_results=3)
        if gl:
            output_parts.append(gl)
            output_parts.append("")

    # The file content itself
    truncated = content[:15000]
    if len(content) > 15000:
        truncated += "\n... (truncated at 15000 chars)"
    output_parts.append(f"<file>\n{truncated}\n</file>\n")

    # Review instructions
    output_parts.append(
        "---\n"
        "## YOUR TASK\n"
        "Review the file above for bugs, security issues, and code quality.\n"
        "For each issue found, report:\n"
        "- Severity: 🔴 CRITICAL / 🟡 WARNING / 🟢 SUGGESTION\n"
        "- Line number\n"
        "- What's wrong and why\n"
        "- The CORRECTED code (copy-pasteable fix, not just a description)\n"
        "- One sentence explaining what the fix does\n\n"
        "Compare against the codebase patterns shown above for consistency.\n"
        "Focus on: OWASP top 10, race conditions, resource leaks, null safety, "
        "async gaps, unbounded growth, hardcoded secrets, SQL injection, path traversal.\n\n"
        "⏩ NEXT STEP: Once you have written your review, call\n"
        "  codewalk_reflect_review(initial_review=<your review text>)\n"
        "  to self-critique and produce an improved final review.\n"
        "  Then per fix: codewalk_approve_action → user yes → codewalk_apply_fix with approval_token."
    )

    return "\n".join(output_parts)

# ─── TOOL 13 [MAINT · user+AI]: codewalk_load_guidelines ────────────
@mcp.tool()
def codewalk_load_guidelines(docs_path: str | None = None) -> str:
    """Load team coding guidelines/standards for use in code reviews.

    Reads guideline documents (.md, .txt, .rst) from the given directory,
    splits them into chunks, embeds them into a dedicated ChromaDB collection,
    and makes them available to codewalk_review_diff automatically.

    Run this once per project. Guidelines persist across reviews in ChromaDB.
    Subsequent calls skip re-embedding if the collection already has data.

    Args:
        docs_path: Path to directory containing guideline files.
                   Falls back to REVIEW_GUIDELINES_PATH env var
                   or settings.review_guidelines_path.

    Returns:
        Success message with count of embedded chunks, or error message.
    """

    from src.codewalk.config import settings
    import os

    path = docs_path or settings.review_guidelines_path
    if not path:
        return (
            "❌ No path provided. Either pass docs_path or set "
            "REVIEW_GUIDELINES_PATH in your .env file."
        )
    
    if not os.path.isdir(path):
        return f"❌ Directory not found: {path}"
    
    store = get_guidelines_store(
        guidelines_path=path,
        persist_dir=state.chroma_path(),
    )
    if not store:
        return f"❌ No guideline files found in {path}"
    
    count = store.chunk_count()

    return (
        f"✅ Loaded {count} guideline chunks from {path}\n"
        f"These will be used automatically in codewalk_review_diff."
    )


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
#     repo_path = settings.repo_path
#     package_root = __name__.rsplit(".", 2)[0]
#     companion_module = f"{package_root}.voice.companion"
#     server_cwd = os.getcwd()
#
#     cmd = (
#         f"cd {shlex.quote(server_cwd)} && "
#         f"REPO_PATH={shlex.quote(repo_path)} "
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
        f"   - User asks what a specific function/class does → `codewalk_explain_function(name)`\n"
        f"   - User asks how something works (concept/flow) → `codewalk_search_codebase(query)`\n"
        f"   - User asks for an overview or summary → `codewalk_get_overview()`\n"
        f"   - User asks about risk or what breaks → `codewalk_get_blast_radius_map(target)`\n"
        f"   - User asks about dependencies or execution flow → `codewalk_get_execution_flow()`\n"
        f"   - User asks where to start reading → `codewalk_get_reading_order()`\n"
        f"   - User asks to review changes → call codewalk_review_diff + codewalk_get_architecture_health + codewalk_get_module_info simultaneously\n"
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
    - Graph stats (files, import edges, DAG status)
    - Bottleneck files (betweenness centrality — most import paths pass through these)
    - Most important files (PageRank — transitively depended on by the most code)
    - Circular dependencies with suggested fixes (which imports to remove)

    Use when asked about code health, architecture quality, refactoring
    priorities, circular imports, or "what should I fix first?"
    """
    if err := _require_index():
        return err

    runtime = state.get_graph_runtime()
    sections = []

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

    Returns the most relevant document chunks with source citations.
    Use this after codewalk_index_docs to find information in team docs.

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
    """Ask a question and get an answer grounded in indexed documents.

    Retrieves relevant document chunks, formats them with source citations,
    and returns context with instructions for answering. Use for questions
    like "How do we deploy?" or "What's our API rate limit?"

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

    Returns a single-use approval_token (required by apply_fix after approval).

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
) -> str:
    """Apply a code fix by replacing old_code with new_code in the file.

    This tool ACTUALLY EDITS FILES ON DISK. Requires approval_token from the
    immediately prior codewalk_approve_action after the user said yes in chat.

    Performs an exact text replacement: searches for old_code in the file and
    replaces it with new_code. Fails if old_code is not found or appears multiple
    times (to prevent accidental replacements).

    Args:
        file_path: Relative path to the file (e.g. "src/auth/login.py")
        old_code: The EXACT code to search for (must match file content precisely)
        new_code: The replacement code
        approval_token: Token from codewalk_approve_action (single-use)

    Returns:
        Success message with the applied change, or error message if replacement failed.
    """
    import os

    global _pending_approval_token
    if not _pending_approval_token or approval_token != _pending_approval_token:
        return (
            "❌ Fix not applied — missing or invalid approval.\n\n"
            "For each issue: call codewalk_approve_action → show the user → wait for yes → "
            "then codewalk_apply_fix with the approval_token from that response."
        )
    _pending_approval_token = None

    repo_path = state.get_repo_path()
    full_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

    # Prevent path traversal outside the repo
    resolved_repo = os.path.realpath(repo_path)
    resolved_target = os.path.realpath(full_path)
    if not resolved_target.startswith(resolved_repo + os.sep) and resolved_target != resolved_repo:
        return f"❌ Invalid file path: {file_path} is outside the repository."

    if not os.path.exists(full_path):
        return f"❌ File not found: {file_path}"

    try:
        with open(full_path, "r", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return f"❌ Cannot read file: {e}"

    # Count occurrences to prevent ambiguous replacements
    count = content.count(old_code)
    if count == 0:
        return (
            f"❌ Could not find the specified code in {file_path}.\n\n"
            f"The old_code you provided does not match the file content exactly.\n"
            f"Please re-read the file and provide the exact text to replace."
        )
    if count > 1:
        return (
            f"❌ Ambiguous replacement: the old_code appears {count} times in {file_path}.\n\n"
            f"Please provide a more specific old_code that includes surrounding context "
            f"(2-3 extra lines above and below) to make the match unique."
        )

    new_content = content.replace(old_code, new_code, 1)

    try:
        with open(full_path, "w", errors="replace") as f:
            f.write(new_content)
    except OSError as e:
        return f"❌ Cannot write file: {e}"

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

    return (
        f"✅ Fix applied to {file_path}\n\n"
        f"---\n"
        f"{diff_preview}\n"
        f"---\n\n"
        f"Moving to the next issue..."
    )


# ─── TOOL 22 [CLOUD · AI]: codewalk_pull_index ─────
@mcp.tool()
def codewalk_pull_index() -> str:
    """Download the latest cloud index, replacing local .codewalk/.
    Requires: CODEWALK_SERVER_URL, CODEWALK_REPO_NAME, CODEWALK_REPO_TOKEN in env."""
    server_url = os.getenv("CODEWALK_SERVER_URL")
    repo_name  = os.getenv("CODEWALK_REPO_NAME")
    repo_token = os.getenv("CODEWALK_REPO_TOKEN")
    if not all([server_url, repo_name, repo_token]):
        return "Not configured for cloud. Set CODEWALK_SERVER_URL, CODEWALK_REPO_NAME, CODEWALK_REPO_TOKEN."

    # Check if already up to date before downloading
    local_meta = _local_manifest_path()
    if local_meta.exists():
        try:
            remote = requests.get(
                f"{server_url}/indexes/{repo_name}/manifest",
                headers={"X-Repo-Token": repo_token},
                timeout=5,
            ).json()
            local_version = json.loads(local_meta.read_text()).get("index_version", 0)
            remote_version = remote.get("index_version", 0)
            if remote_version <= local_version:
                return f"Already up to date (v{local_version})."
        except Exception:
            pass  # If manifest check fails, proceed with download

    _download_index(server_url, repo_name, repo_token)
    local_meta_after = _local_manifest_path()
    new_version = ""
    if local_meta_after.exists():
        try:
            new_version = f" (v{json.loads(local_meta_after.read_text()).get('index_version', '?')})"
        except Exception:
            pass
    return f"Index updated{new_version}. Using latest version now."

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
def codewalk_connect_repo(repo_name: str, repo_token: str) -> str:
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

    # Step 4: Remove stale local index (same as codewalk_pull_index / _download_index)
    codewalk_dir = Path(git_root) / ".codewalk"
    if codewalk_dir.exists():
        shutil.rmtree(codewalk_dir)

    # Step 5: Download and extract index tarball
    try:
        dl_resp = requests.get(
            f"{server_url}/indexes/{repo_name}",
            headers={"X-Repo-Token": repo_token},
            stream=True, timeout=300,
        )
        dl_resp.raise_for_status()
        tarball = Path("/tmp/codewalk-connect-index.tar.gz")
        with open(tarball, "wb") as fh:
            for chunk in dl_resp.iter_content(chunk_size=8192):
                fh.write(chunk)
    except Exception as exc:
        return f"❌ Download failed: {exc}"

    try:
        subprocess.run(
            ["tar", "-xzf", str(tarball), "-C", git_root],
            check=True,
        )
        tarball.unlink(missing_ok=True)
    except Exception as exc:
        return f"❌ Extraction failed: {exc}"

    # Write CODEWALK_REPO_NAME and CODEWALK_REPO_TOKEN into local mcp.json if found
    mcp_json_path = Path(git_root) / "mcp.json"
    mcp_hint = ""
    if mcp_json_path.exists():
        try:
            mcp_cfg = json.loads(mcp_json_path.read_text())
            env = mcp_cfg.setdefault("env", {})
            env["CODEWALK_REPO_NAME"] = repo_name
            env["CODEWALK_REPO_TOKEN"] = repo_token
            mcp_json_path.write_text(json.dumps(mcp_cfg, indent=2))
            mcp_hint = "\n  mcp.json updated with repo credentials."
        except Exception:
            mcp_hint = "\n  ⚠️  Could not update mcp.json — update CODEWALK_REPO_NAME and CODEWALK_REPO_TOKEN manually."
    else:
        mcp_hint = (
            "\n  Add to mcp.json env:\n"
            f'    "CODEWALK_REPO_NAME": "{repo_name}",\n'
            f'    "CODEWALK_REPO_TOKEN": "{repo_token}"'
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
    from src.codewalk import __version__

    server_url = os.getenv("CODEWALK_SERVER_URL", "")
    if not server_url:
        return "Cloud not configured. Set CODEWALK_SERVER_URL to enable version checks."

    try:
        remote = requests.get(f"{server_url}/version", timeout=5).json()
    except Exception as exc:
        return f"Could not check version: {exc}"

    local_version = __version__
    remote_version = remote.get("codewalk_version", "unknown")
    remote_sha = remote.get("commit_sha", "")[:7]

    if local_version == remote_version:
        return f"Codewalk is up to date (v{local_version})."

    return (
        f"🆕 Codewalk update available!\n"
        f"  Local:    v{local_version}\n"
        f"  Cloud:    v{remote_version} ({remote_sha})\n"
        f"  Released: {remote.get('released_at', '?')}\n"
        f"  Notes:    {remote.get('release_notes_url', '')}\n"
        f"  Update:   {remote.get('update_command', 'git pull origin master')}"
    )


# ─── Shared tool lookup map (used by voice_ask, backends.py) ──
# NOTE: Must be defined AFTER all tool functions.
_TOOL_MAP = {
    "codewalk_analyze_codebase": codewalk_analyze_codebase,
    "codewalk_search_codebase": codewalk_search_codebase,
    "codewalk_get_module_info": codewalk_get_module_info,
    "codewalk_explain_function": codewalk_explain_function,
    "codewalk_get_overview": codewalk_get_overview,
    "codewalk_get_blast_radius_map": codewalk_get_blast_radius_map,
    "codewalk_get_reading_order": codewalk_get_reading_order,
    "codewalk_get_execution_flow": codewalk_get_execution_flow,
    "codewalk_incremental_reindex": codewalk_incremental_reindex,
    "codewalk_refresh_analysis": codewalk_refresh_analysis,
    "codewalk_review_diff": codewalk_review_diff,
    "codewalk_reflect_review": codewalk_reflect_review,
    "codewalk_review_file": codewalk_review_file,
    "codewalk_load_guidelines": codewalk_load_guidelines,
    "codewalk_get_architecture_health": codewalk_get_architecture_health,
    "codewalk_call_chain": codewalk_call_chain,
    "codewalk_index_docs": codewalk_index_docs,
    "codewalk_search_docs": codewalk_search_docs,
    "codewalk_ask_docs": codewalk_ask_docs,
    "codewalk_approve_action": codewalk_approve_action,
    "codewalk_apply_fix": codewalk_apply_fix,
    "codewalk_voice_ask": codewalk_voice_ask,
    "codewalk_speak": codewalk_speak,
    "codewalk_pull_index": codewalk_pull_index,
    "codewalk_index_status": codewalk_index_status,
    "codewalk_connect_repo": codewalk_connect_repo,
    "codewalk_check_version": codewalk_check_version,
}

if __name__ == "__main__":
    mcp.run(transport="stdio")