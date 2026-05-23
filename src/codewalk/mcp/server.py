"""
=============================================================================
 server.py - MCP Server (18 Tools for VS Code Copilot Integration)
=============================================================================

WHAT THIS FILE DOES:
    The MCP (Model Context Protocol) server that VS Code Copilot connects to.
    Exposes 18 tools that Copilot can call to analyze, search, review, and
    speak about a codebase.

TOOL CATEGORIES:
    SETUP  (Tools 1, 9-11): Analyze repo, scan/filter files, index embeddings
    QUERY  (Tools 2-8):     Search code, explain functions, blast radius,
                            reading order, execution flow, module info, overview
    MAINT  (Tools 12-16):   Incremental reindex, refresh analysis, review
                            diff/file, load guidelines
    VOICE  (Tools 17-18):   Mic record+transcribe, TTS speak

HOW IT INTEGRATES:
    1. VS Code reads mcp.json which points to this server
    2. Copilot spawns `python -m src.codewalk.mcp.server` over stdio
    3. Copilot reads the `instructions` field to know how to use tools
    4. User asks a question -> Copilot picks the right tool -> tool returns data

WHERE IT'S CALLED:
    - VS Code Copilot via MCP protocol (stdio transport)
    - voice/backends.py -> _TOOL_MAP for direct execution
    - Can also run standalone: `python -m src.codewalk.mcp.server`

DEPENDENCIES:
    - All analysis/, generation/, review/, voice/ modules
    - api/state.py: shared state singleton
    - pipeline.py: indexing functions
    - FastMCP: MCP protocol implementation

=============================================================================
"""

import inspect
import logging
import subprocess
import sys
from fnmatch import fnmatch
import asyncio

from mcp.server.fastmcp import FastMCP

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context
from src.codewalk.pipeline import full_index_parallel, index_from_paths_parallel, incremental_reindex
from src.codewalk.ingestion.file_filter import should_skip
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
        "1) codewalk_analyze_codebase — detect modules and structure.\n"
        "   → If the response says 'INDEX READY', SKIP steps 2-6 and go straight to answering questions.\n"
        "   → If the response says 'INDEX EMPTY', continue with steps 2-6.\n"
        "2) codewalk_scan_files(batch=1) — get file paths for filtering.\n"
        "3) Review paths. KEEP: source code, business logic, services, models, "
        "controllers, UI, entry points, config with logic. "
        "SKIP: tests, generated code, assets, docs, lock files, migrations, "
        "CI/CD, vendor/node_modules, IDE configs, __pycache__. When in doubt, keep it.\n"
        "4) codewalk_submit_filtered_files — submit relevant paths from this batch.\n"
        "5) Repeat 2-4 (increment batch) until response says LAST BATCH.\n"
        "6) codewalk_index_filtered_files — embed all selected files.\n"
        "\n"
        "## ANSWERING QUESTIONS (after setup)\n"
        "- 'What does X do?' → codewalk_explain_function(X) — line-by-line explanation\n"
        "- 'How does feature Y work?' → codewalk_search_codebase(Y)\n"
        "- 'Give me an overview' → codewalk_get_overview\n"
        "- 'What's in module Z?' → codewalk_get_module_info(Z) — files + functions/classes\n"
        "- 'What breaks if I change X?' → codewalk_get_blast_radius_map(target=X) — "
        "X can be a module name, file name, or empty for top 30 riskiest\n"
        "- 'Where should I start reading?' → codewalk_get_reading_order — returns ALL files\n"
        "- 'Show me the dependency flow' → codewalk_get_execution_flow — "
        "no arg = module-to-module flow, with module_name = file-to-file flow\n"
        "\n"
        "## MAINTENANCE (after code changes)\n"
        "- codewalk_incremental_reindex — re-embed only changed files (hash-based skip)\n"
        "- codewalk_refresh_analysis — rebuild deps/modules without re-embedding\n"
        "\n"
        "## CODE REVIEW\n"
        "- codewalk_review_diff — review git diff for bugs, security, style\n"
        "- codewalk_review_file(path) — review one file against codebase patterns\n"
        "- codewalk_load_guidelines(path) — load team coding standards for reviews\n"
        "\n"
        "## VOICE COMPANION\n"
        "- codewalk_voice_ask — record mic + transcribe, then YOU:\n"
        "    1. Call the right codewalk tool\n"
        "    2. Show the FULL result as text in the chat (same detail as typed)\n"
        "    3. Call codewalk_speak() with a 2-4 sentence spoken summary\n"
        "- codewalk_speak(text) — speak a plain-English summary aloud via TTS\n"
        "\n"
        "## PRESENTING BLAST RADIUS RESULTS\n"
        "When showing blast radius or overview results, separate files into two groups:\n"
        "1. **Core / Foundational** (design system, utils, extensions, config, constants,\n"
        "   shared widgets, theme files, base classes) — summarize briefly, e.g.\n"
        "   '12 design system files are high-risk as expected.'\n"
        "2. **Business Logic** (screens, controllers, services, repositories, blocs,\n"
        "   cubits, use cases, API clients) — show these in full detail.\n"
        "Lead with the Business Logic section — that's what the user cares about.\n"
        "Foundational files being high-risk is expected and not actionable.\n"
        "\n"
        "## ERROR HANDLING\n"
        "If any tool returns a message starting with 'Error:':\n"
        "- 'No codebase indexed' → tell user to run codewalk_analyze_codebase first\n"
        "- 'Module not found' → show the available modules from the error message\n"
        "- Never retry the same tool with identical arguments after an error\n"
    ),
)

# ─── MCP-only batch filtering state ──────────────────────────────────
_all_scanned_files: list[dict] = []
_selected_file_paths: list[str] = []
MCP_BATCH_SIZE = 100  # files per batch for Copilot filtering


# ══════════════════════════════════════════════════════════════════════
#  SETUP TOOLS — user or AI runs these to onboard a codebase
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 1 [SETUP · user+AI]: codewalk_analyze_codebase ────────────
@mcp.tool()
def codewalk_analyze_codebase() -> str:
    """Analyze a codebase structure and auto-index for search.

    This must be called FIRST. It will:
    1. Detect modules, dependencies, and blast radius
    2. If an index already exists → skip to ready state (INDEX READY)
    3. If no index exists → auto-filter files (skip tests/node_modules/etc)
       and embed them for semantic search

    No additional steps needed — this tool handles everything.
    After this returns, query tools are ready to use.

    EXAMPLE TRACE (fatih/color — 9 Go files):
        settings.repo_path          = "data/repos/fatih/color"
        state._files                = [{"file_path": "color.go", "language": "go", "size": 8293}, ...] (9 files)
        state._modules_result       = {"modules": {"color": {"files": [...], "file_count": 9}}, ...}
        existing (ChromaDB count)   = 0  → INDEX EMPTY path
        all_paths (after skip filter) = ["color.go", "color_test.go", ...] → 7 files (2 skipped)
        result["chunks_embedded"]   = 348
        return → "Files found: 9\nFiles indexed: 7\nChunks embedded: 348\nModules: color"

    EXAMPLE TRACE (existing index — codewalk src, 56 files):
        existing (ChromaDB count)   = 1842  → INDEX READY path
        return → "INDEX READY — 1842 chunks available. SKIP scan/filter/index."

    ⏩ NEXT STEP: Use any query tool (codewalk_get_overview, codewalk_search_codebase, etc.)
    """
    _log(f"[codewalk_analyze_codebase] Starting analysis: {settings.repo_path}")
    state.rebuild_analysis_cache()

    # Check if there's an existing index for search.
    state._store = VectorStore(persist_dir=state.chroma_path())
    state._store.create_collection(state.get_collection_name())
    existing = state._store.collection.count()

    modules = list(state._modules_result["modules"].keys())
    _log(f"[codewalk_analyze_codebase] Modules: {modules} | Index: {existing} chunks")
    if existing > 0:
        return (
            f"Codebase analyzed successfully.\n"
            f"Files found: {len(state._files)}\n"
            f"Modules found: {', '.join(modules)}\n"
            f"Search index: INDEX READY — {existing} chunks available.\n\n"
            f"✅ Index already exists. SKIP scan/filter/index steps.\n"
            f"Ready to answer questions — use query tools directly."
        )

    # ── Auto-filter: use built-in skip patterns (no user intervention) ──
    _log("[codewalk_analyze_codebase] INDEX EMPTY — auto-filtering and indexing...")
    all_paths = [f["file_path"] for f in state._files if not should_skip(f["file_path"])]
    _log(f"[codewalk_analyze_codebase] Auto-filtered to {len(all_paths)} files (from {len(state._files)} total)")

    if not all_paths:
        return (
            f"Codebase analyzed successfully.\n"
            f"Files found: {len(state._files)}\n"
            f"Modules found: {', '.join(modules)}\n"
            f"⚠️ No indexable files found after filtering.\n"
            f"Check .codewalkignore or file patterns."
        )

    # ── Auto-index: chunk + embed + store ──
    result = index_from_paths_parallel(
        all_paths, settings.repo_path,
        state.get_collection_name(),
        persist_dir=state.chroma_path()
    )

    # Refresh store reference
    state._store = VectorStore(persist_dir=state.chroma_path())
    state._store.create_collection(state.get_collection_name())

    _log(f"[codewalk_analyze_codebase] Indexed {result['chunks_embedded']} chunks in {result.get('total_time', 'N/A')}")

    return (
        f"Codebase analyzed and indexed successfully.\n"
        f"Files found: {len(state._files)}\n"
        f"Files indexed: {result['files_scanned']}\n"
        f"Chunks embedded: {result['chunks_embedded']}\n"
        f"Time: {result.get('total_time', 'N/A')}\n"
        f"Modules found: {', '.join(modules)}\n\n"
        f"✅ Ready to answer questions — use query tools directly."
    )

# ══════════════════════════════════════════════════════════════════════
#  QUERY TOOLS — user asks a question, AI picks the right tool
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 2 [QUERY · user+AI]: codewalk_search_codebase ──────────────
@mcp.tool()
def codewalk_search_codebase(query: str) -> str:
    """Search the codebase using ChromaDB semantic embeddings — NOT a text search.

    Uses vector similarity on pre-computed embeddings to find code by meaning,
    not keywords. Finds results that keyword search would miss.

    Returns up to 5 relevant code snippets with file paths, line numbers,
    and surrounding context.

    For a specific function/class by name, prefer codewalk_explain_function.

    EXAMPLE TRACE (fatih/color, query="ANSI color codes"):
        results = store.search("ANSI color codes", n_results=5)
        results = [
            {"text": "func (c *Color) Add(value ...Attribute)...", "metadata": {"file_path": "color.go", "symbol_name": "Add", "start_line": 72}},
            {"text": "const (Reset Attribute = iota...)",          "metadata": {"file_path": "color.go", "symbol_name": "",    "start_line": 14}},
            ...  # 5 results total
        ]
        return → format_context(results)  # "--- color.go | function: Add (lines 72-89) ---\n..."

    Args:
        query: Natural language search query, e.g. "authentication logic",
               "error handling in API routes", "how files get chunked"
    """
    state.ensure_initialized()
    if state._store is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

    _log(f"[codewalk_search_codebase] Query: {query}")
    results = state._store.search(query, n_results=5)
    _log(f"[codewalk_search_codebase] Found {len(results)} results")
    if not results:
        return "No relevant code found for that query."
    return format_context(results)

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
    state.ensure_initialized()
    if state._modules_result is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

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
    state.ensure_initialized()
    if state._store is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

    _log(f"[codewalk_explain_function] Looking up: {function_name}")
    return explain_function_text(state._store, function_name, state._deps, state._graph_runtime)

# ─── TOOL 5 [QUERY · user+AI]: codewalk_get_overview ─────────────────
@mcp.tool()
def codewalk_get_overview() -> str:
    """Get the project overview from Codewalk's computed analysis.

    Returns:
    - Tech stack detection results
    - Module list with file counts and languages
    - Module dependency flow (entry points → core modules)
    - Top 30 riskiest files by blast radius with break chains

    EXAMPLE TRACE (fatih/color):
        state._repo_path           = "data/repos/fatih/color"
        state._modules_result      = {"modules": {"color": {"file_count": 9, "languages": {"go": 9}}}}
        state._deps["graph"]       = {"color.go": ["doc.go"], "color_test.go": ["color.go"], ...}
        return → "## Tech Stack\n- Go (9 files)\n## Modules\n- color (9 files)\n## Top Riskiest\n1. color.go — 5 dependents"
    """
    state.ensure_initialized()
    if state._modules_result is None or state._repo_path is None or state._deps is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

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
    state.ensure_initialized()
    if state._modules_result is None or state._repo_path is None or state._deps is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

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
    state.ensure_initialized()
    if state._modules_result is None or state._files is None or state._deps is None:
        return "Error: No codebase indexed yet."

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
    state.ensure_initialized()
    if state._modules_result is None or state._repo_path is None or state._deps is None:
        return "Error: No codebase indexed yet."

    _log(f"[codewalk_get_execution_flow] module={module_name or 'repo-level'}")
    return execution_flow_text(state._modules_result, state._deps, module_name)


# ══════════════════════════════════════════════════════════════════════
#  SETUP TOOLS (continued) — user+AI indexing pipeline
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 9 [SETUP · user+AI]: codewalk_scan_files ──────────────────
@mcp.tool()
def codewalk_scan_files(batch: int = 1) -> str:
    """Get the next batch of file paths for filtering.

    Returns ~100 file paths per batch. Review them and submit the relevant
    ones via codewalk_submit_filtered_files before calling the next batch.

    KEEP: .py, .ts, .js, .dart, .go, .rs, .java files with business logic,
          services, models, controllers, entry points, meaningful configs.
    SKIP: tests/, __pycache__/, node_modules/, .git/, assets/, docs/,
          *.lock, *.generated.*, migrations/, .github/, IDE configs.

    When response says LAST BATCH → call codewalk_index_filtered_files.

    ⏩ NEXT STEP: codewalk_submit_filtered_files(paths=[...relevant paths from this batch...])
    ⏪ PREVIOUS STEP: codewalk_analyze_codebase

    Args:
        batch: Batch number (1-indexed). Start with 1, increment each call.
    """
    global _all_scanned_files

    repo_path = settings.repo_path
    _log(f"[codewalk_scan_files] Batch {batch} requested")

    # Only scan once (batch 1), reuse for subsequent batches
    if batch == 1:
        _all_scanned_files = scan_directory(repo_path)
        _log(f"[codewalk_scan_files] Scanned {len(_all_scanned_files)} total files")

        # Apply EXCLUDE_PATHS filter
        exclude_raw = settings.exclude_paths.strip()
        if exclude_raw:
            patterns = [p.strip() for p in exclude_raw.split(",") if p.strip()]
            before = len(_all_scanned_files)
            _all_scanned_files = [
                f for f in _all_scanned_files
                if not any(
                    fnmatch(f["file_path"], pat) or f["file_path"].startswith(pat.rstrip("/") + "/") or ("/" + pat.rstrip("/") + "/") in ("/" + f["file_path"])
                    for pat in patterns
                )
            ]
            excluded = before - len(_all_scanned_files)
            _log(f"[codewalk_scan_files] EXCLUDE_PATHS removed {excluded} files (patterns: {patterns})")

    total = len(_all_scanned_files)
    total_batches = (total + MCP_BATCH_SIZE - 1) // MCP_BATCH_SIZE
    start = (batch - 1) * MCP_BATCH_SIZE
    end = min(start + MCP_BATCH_SIZE, total)

    if start >= total:
        return f"No more batches. Total files: {total}. Call codewalk_index_filtered_files to embed."

    batch_files = _all_scanned_files[start:end]
    paths = [f["file_path"] for f in batch_files]

    is_last = batch >= total_batches
    _log(f"[codewalk_scan_files] Returning batch {batch}/{total_batches} ({len(paths)} files) {'LAST' if is_last else ''}")
    status = "LAST BATCH — after submitting, call codewalk_index_filtered_files" if is_last else f"More batches remain — call codewalk_scan_files(batch={batch + 1}) next"

    next_step = (
        f"\n\n⏩ NEXT STEP: Call codewalk_submit_filtered_files with the relevant paths from this batch.\n"
        f"(If the AI doesn't call it automatically, run it yourself.)"
    )

    return (
        f"Batch {batch}/{total_batches} ({len(paths)} files, {total} total)\n"
        f"{status}\n\n"
        + "\n".join(paths)
        + next_step
    )


# ─── TOOL 10 [SETUP · user+AI]: codewalk_submit_filtered_files ──────
@mcp.tool()
def codewalk_submit_filtered_files(paths: list[str]) -> str:
    """Submit the relevant file paths from the current codewalk_scan_files batch.

    Only submit paths you want indexed. Accepts file paths and directory
    paths (a directory matches all files under it).
    Call once per batch, then call codewalk_scan_files for the next batch.

    Do NOT call with an empty list — skip the call if no files are relevant.

    ⏩ NEXT STEP: codewalk_scan_files(batch=<next_batch_number>) OR codewalk_index_filtered_files (if last batch)
    ⏪ PREVIOUS STEP: codewalk_scan_files

    Args:
        paths: File/directory paths from the current batch to index,
               e.g. ["src/services/auth.py", "models/"]
    """
    global _selected_file_paths
    _selected_file_paths.extend(paths)
    _log(f"[codewalk_submit_filtered_files] Added {len(paths)} paths (total: {len(_selected_file_paths)})")
    return f"Added {len(paths)} paths. Total selected so far: {len(_selected_file_paths)}\n\n" \
           f"⏩ NEXT STEP: Call codewalk_scan_files(batch=<next_batch_number>) for the next batch, " \
           f"or codewalk_index_filtered_files if all batches are done.\n" \
           f"(If the AI doesn't call it automatically, run it yourself.)"


# ─── TOOL 11 [SETUP · user+AI]: codewalk_index_filtered_files ───────
@mcp.tool()
def codewalk_index_filtered_files() -> str:
    """Index all files submitted via codewalk_submit_filtered_files.

    Call this after processing all codewalk_scan_files batches.
    Chunks, embeds, and stores the selected files for search.

    ⏩ NEXT STEP: codewalk_get_overview (then any query tool)
    ⏪ PREVIOUS STEP: codewalk_submit_filtered_files (last batch)
    """
    global _selected_file_paths

    repo_path = settings.repo_path
    selected_count = len(_selected_file_paths)
    _log(f"[codewalk_index_filtered_files] Starting indexing of {selected_count} selected paths")

    # Open (or create) the persistent collection WITHOUT wiping it first
    working_store = VectorStore(persist_dir=state.chroma_path())
    working_store.create_collection(state.get_collection_name())
    existing_count = working_store.collection.count()

    if existing_count > 0:
        # ── Incremental path: existing index found ──────────────────────
        # Skip files whose content hash hasn't changed; only re-embed changed/new ones.
        _log(f"[codewalk_index_filtered_files] Existing index ({existing_count} chunks) — using incremental reindex")
        result = incremental_reindex(_selected_file_paths, repo_path, state.get_collection_name(), persist_dir=state.chroma_path())

        state._store = working_store
        state.rebuild_analysis_cache()
        modules = list(state._modules_result["modules"].keys())
        _selected_file_paths = []

        return (
            f"Incremental index update complete (existing index had {existing_count} chunks).\n"
            f"Selected paths: {selected_count}\n"
            f"Files on disk: {result['files_on_disk']}\n"
            f"Skipped (unchanged): {result['files_skipped']}\n"
            f"Re-indexed (changed/new): {result['files_reindexed']}\n"
            f"Deleted (removed from disk): {result['files_deleted']}\n"
            f"Chunks embedded: {result['chunks_embedded']}\n"
            f"Time: {result['total_time']}\n"
            f"Modules found: {', '.join(modules)}\n\n"
            f"Ready! You can now use: codewalk_get_overview, codewalk_search_codebase, etc."
        )
    else:
        # ── Full-index path: no existing data ──────────────────────────
        result = index_from_paths_parallel(_selected_file_paths, repo_path, state.get_collection_name(), persist_dir=state.chroma_path())

        state._store = working_store
        state.rebuild_analysis_cache()
        modules = list(state._modules_result["modules"].keys())
        _selected_file_paths = []

        _log(f"[codewalk_index_filtered_files] Done: {result['files_scanned']} files, {result['chunks_embedded']} chunks embedded")

        return (
            f"Indexed {result['files_scanned']} files (from {selected_count} selected paths).\n"
            f"Chunks created: {result['chunks_created']}\n"
            f"Chunks embedded: {result['chunks_embedded']}\n"
            f"Time: {result.get('total_time', 'N/A')}\n"
            f"Steps: {' | '.join(result.get('steps', []))}\n"
            f"Modules found: {', '.join(modules)}\n\n"
            f"Ready! You can now use these tools:\n"
            f"  - codewalk_get_overview — project summary\n"
            f"  - codewalk_search_codebase — search code by concept\n"
            f"  - codewalk_get_module_info — inspect a specific module\n"
            f"  - codewalk_explain_function — explain any function/class\n"
            f"  - codewalk_get_blast_radius_map — check change risk\n"
            f"  - codewalk_get_reading_order — optimal file reading order\n"
            f"  - codewalk_get_execution_flow — dependency flow diagram"
        )

# ─── TOOL 12 [MAINT · user+AI]: codewalk_incremental_reindex ────────
@mcp.tool()
def codewalk_incremental_reindex() -> str:
    """Re-index only files that changed since last indexing.

    Compares content hashes stored in ChromaDB metadata against current
    file content on disk. Skips unchanged files, re-embeds changed ones,
    and removes chunks for deleted files. Much faster than full re-index.

    Requires: codebase must be indexed at least once via the full setup
    workflow (scan → filter → index). After that, call this tool whenever
    code changes to keep embeddings in sync.

    Returns a summary showing how many files were skipped, re-indexed,
    or deleted, plus the number of new chunks embedded.

    ⏪ PREVIOUS STEP: codewalk_index_filtered_files (first-time setup)
    ⏩ NEXT STEP: any query tool (search, explain, blast radius, etc.)
    """
    repo_path = settings.repo_path
    if not repo_path:
        return "❌ No repo path set. Run codewalk_analyze_codebase first."

    # Initialise _store from disk if codewalk_analyze_codebase hasn't been called this session

    if not state._store or not state._store.collection:
        state._store = VectorStore(persist_dir=state.chroma_path())
        state._store.create_collection(state.get_collection_name())

    if state._store.collection.count() == 0:
        return "❌ No index exists. Run the full setup workflow first (scan → filter → index)."

    # Use previously selected paths if available, else fall back to all indexed files
    paths = list(_selected_file_paths) if _selected_file_paths else list(state._store.get_all_indexed_files())
    if not paths:
        return "❌ No files to reindex. Run the full setup workflow first."
    
    result = incremental_reindex(paths, repo_path, state.get_collection_name(), persist_dir=state.chroma_path())

    state.rebuild_analysis_cache()

    return (
        f"Incremental reindex complete ({result['total_time']})\n\n"
        f"  Files on disk:   {result['files_on_disk']}\n"
        f"  Skipped (same):  {result['files_skipped']}\n"
        f"  Re-indexed:      {result['files_reindexed']}\n"
        f"  Deleted:         {result['files_deleted']}\n"
        f"  Chunks embedded: {result['chunks_embedded']}\n\n"
        f"Analysis cache refreshed."
    )

# ══════════════════════════════════════════════════════════════════════
#  MAINTENANCE TOOLS — user or AI can call after code changes
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 13 [MAINT · user+AI]: codewalk_refresh_analysis ────────────
@mcp.tool()
def codewalk_refresh_analysis() -> str:
    """Refresh the cached analysis without re-embedding.

    Re-scans files, rebuilds dependency graph, and re-detects modules.
    Use this after code changes to update blast radius, reading order,
    and module structure. Does NOT re-index or re-embed — embeddings
    stay as they are. For re-embedding, use the full setup workflow.
    """
    state.ensure_initialized()
    if state._repo_path is None:
        return "Error: No codebase analyzed yet. Call codewalk_analyze_codebase first."

    _log("[codewalk_refresh_analysis] Refreshing cached analysis...")
    state.rebuild_analysis_cache()

    modules = list(state._modules_result["modules"].keys())
    return (
        f"Analysis refreshed (no re-embedding).\n"
        f"Files: {len(state._files)}\n"
        f"Dependency graph: {len(state._deps['graph'])} files\n"
        f"Modules: {', '.join(modules)}"
    )


# ─── TOOL 14 [MAINT · user+AI]: codewalk_review_diff ────────────
@mcp.tool()
def codewalk_review_diff(
    staged: bool = False,
    target_branch: str | None = None,
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
    """
    from src.codewalk.review.reviewer import prepare_review_context

    ctx = prepare_review_context(
        staged=staged,
        target_branch=target_branch,
        store=state._store,
        deps=state._deps,
        repo_path=settings.repo_path,
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
        "- Suggested fix\n\n"
        "Focus on: OWASP top 10 (injection, auth bypass, XSS, SSRF, open redirect), "
        "race conditions, resource leaks, null safety, async gaps (setState after await "
        "without mounted check), unbounded growth, hardcoded secrets, certificate pinning "
        "bypass, SQL injection, path traversal. Be AGGRESSIVE — better to over-flag."
    )

    return "\n".join(output_parts)

# ─── TOOL 15 [MAINT · user+AI]: codewalk_review_file ────────────
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

    repo_path = settings.repo_path
    full_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

    if not os.path.exists(full_path):
        return f"❌ File '{file_path}' not found."

    try:
        content = open(full_path, "r", errors="replace").read()
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
    caller_ctx = _get_caller_context(synthetic_diff, state._deps)
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
        results = state._store.search(f"code in {file_path}", n_results=5)
        if results:
            output_parts.append("## Similar patterns elsewhere in the codebase")
            output_parts.append(format_context(results))
            output_parts.append("")

    # Guidelines
    guidelines_store = get_guidelines_store()
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
        "- Suggested fix\n\n"
        "Compare against the codebase patterns shown above for consistency.\n"
        "Focus on: OWASP top 10, race conditions, resource leaks, null safety, "
        "async gaps, unbounded growth, hardcoded secrets, SQL injection, path traversal."
    )

    return "\n".join(output_parts)

# ─── TOOL 16 [MAINT · user+AI]: codewalk_load_guidelines ────────────
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
    
    store = get_guidelines_store()
    if not store:
        return f"❌ No guideline files found in {path}"
    
    count = store.collection.count()

    return (
        f"✅ Loaded {count} guideline chunks from {path}\n"
        f"These will be used automatically in codewalk_review_diff."
    )


# ══════════════════════════════════════════════════════════════════════
#  VOICE TOOL — natural language interface to all Codewalk tools
# ══════════════════════════════════════════════════════════════════════

# ─── TOOL 17 (COMMENTED OUT) codewalk_start_voice ────────────────────
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
#     Requires: Ollama running with qwen2.5:1.5b pulled.
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


# ─── Shared tool lookup map (used by voice_ask, backends.py) ──
_TOOL_MAP = {
    "codewalk_analyze_codebase": codewalk_analyze_codebase,
    "codewalk_search_codebase": codewalk_search_codebase,
    "codewalk_get_module_info": codewalk_get_module_info,
    "codewalk_explain_function": codewalk_explain_function,
    "codewalk_get_overview": codewalk_get_overview,
    "codewalk_get_blast_radius_map": codewalk_get_blast_radius_map,
    "codewalk_get_reading_order": codewalk_get_reading_order,
    "codewalk_get_execution_flow": codewalk_get_execution_flow,
    "codewalk_scan_files": codewalk_scan_files,
    "codewalk_submit_filtered_files": codewalk_submit_filtered_files,
    "codewalk_index_filtered_files": codewalk_index_filtered_files,
    "codewalk_incremental_reindex": codewalk_incremental_reindex,
    "codewalk_refresh_analysis": codewalk_refresh_analysis,
    "codewalk_review_diff": codewalk_review_diff,
    "codewalk_review_file": codewalk_review_file,
    "codewalk_load_guidelines": codewalk_load_guidelines,
}


# ─── TOOL 17 [VOICE · user]: codewalk_voice_ask ──────────────────────
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

    # ── 0. Stop any playing audio + beep to signal "start talking" ──
    stop_speaking()
    subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"], check=False)

    # ── 1. Record from mic ──────────────────────────────────────────
    _log("[codewalk_voice_ask] Recording from mic...")
    try:
        audio = record_audio()
    except Exception as e:
        return f"❌ Mic recording failed: {e}\n\nCheck microphone permissions in System Settings → Privacy → Microphone."

    if len(audio) == 0:
        return "❌ No audio captured. Make sure your microphone is working."

    # ── Beep to signal "recording stopped" ──
    subprocess.run(["afplay", "/System/Library/Sounds/Pop.aiff"], check=False)

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
    subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"], check=False)

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
        f"   - User asks to review changes → `codewalk_review_diff()`\n"
        f"   - DEFAULT: if user names something that could be a module → `codewalk_get_module_info`, otherwise → `codewalk_search_codebase`\n"
        f"2. Show the FULL tool result as text in the chat — same detail as a typed question.\n"
        f"3. Then call `codewalk_speak()` with a 2-4 sentence plain-English spoken summary.\n"
        f"⚠️ NEVER skip step 2 or 3. NEVER pass the full tool output to speak — summarize it."
    )


# ─── TOOL 18 [VOICE · user]: codewalk_speak ──────────────────────────
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


if __name__ == "__main__":
    mcp.run(transport="stdio")