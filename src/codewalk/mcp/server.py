"""Codewalk MCP server — 18 tools for codebase onboarding, search, review, and voice.

Tool categories:
  SETUP  (1, 9-11): Analyze repo, scan/filter files, index embeddings.
  QUERY  (2-8):     Search code, explain functions, blast radius, reading order, execution flow.
  MAINT  (12-16):   Incremental reindex, refresh analysis, review diff/file, load guidelines.
  VOICE  (17-18):   Mic record+transcribe (Copilot routes), TTS speak.
"""

import inspect
import logging
import subprocess
import sys
from fnmatch import fnmatch
import asyncio

from mcp.server.fastmcp import FastMCP

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.generation.module_explainer import explain_module
from src.codewalk.generation.overview_generator import generate_overview
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context
from src.codewalk.pipeline import full_index_parallel, index_from_paths_parallel, incremental_reindex
from src.codewalk.ingestion.file_filter import should_skip
from src.codewalk.config import settings, get_llm
from src.codewalk.analysis.blast_radius import (
    get_blast_radius,
    calculate_full_blast_map,
)
from src.codewalk.analysis.reading_order import generate_reading_order_raw
from src.codewalk.review.reviewer import review_diff
from src.codewalk.review.guidelines_loader import get_guidelines_store
from src.codewalk.voice.stt import record_audio, transcribe
from src.codewalk.voice.tts import speak, stop_speaking
from src.codewalk.api import state


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
        "X can be a module name, file name, or empty for top 15 riskiest\n"
        "- 'Where should I start reading?' → codewalk_get_reading_order — returns ALL files\n"
        "- 'Show me the dependency flow' → codewalk_get_execution_flow — "
        "no arg = module-to-module flow, with module_name = file-to-file flow\n"
        "\n"
        "## MAINTENANCE (after code changes)\n"
        "- codewalk_incremental_reindex — re-embed only changed files (hash-based skip)\n"
        "- codewalk_refresh_analysis — rebuild deps/modules without re-embedding\n"
        "\n"
        "## CODE REVIEW\n"
        "- codewalk_review_diff — review git diff for bugs, security, style (LLM + pre-checks)\n"
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

    # Case-insensitive lookup
    actual_name = None
    for name in modules:
        if name.lower() == module_name.lower():
            actual_name = name
            break

    # Fallback: search for sub-folder inside modules (e.g. "users" → "features/users")
    matched_as_feature = False
    if actual_name is None:
        source_root = state._modules_result.get("source_root", "")
        for mod_name, mod_info in modules.items():
            # Look through files in each module for a sub-folder matching the query
            prefix = f"{source_root}/{mod_name}/{module_name.lower()}/" if source_root else f"{mod_name}/{module_name.lower()}/"
            matching_files = [f for f in mod_info["files"] if f.lower().startswith(prefix.lower())]
            if matching_files:
                # Found as a sub-folder — synthesize a feature-level view
                actual_name = mod_name
                matched_as_feature = True
                # Override info with just this sub-folder's files
                info_override = {
                    "files": matching_files,
                    "file_count": len(matching_files),
                    "languages": {},
                }
                from collections import Counter
                lang_counter = Counter()
                matching_set = set(matching_files)
                for f in state._files or []:
                    if f["file_path"] in matching_set:
                        lang_counter[f["language"]] += 1
                info_override["languages"] = dict(lang_counter)
                break

    if actual_name is None:
        available = ", ".join(sorted(modules.keys()))
        return f"Module '{module_name}' not found. Available modules: {available}\n\nTip: Try the parent module name (e.g. 'features' instead of a specific feature)."

    # Use overridden info if matched as a feature sub-folder
    if matched_as_feature:
        info = info_override
    else:
        info = modules[actual_name]

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
        name = file_path.split("/")[-1]
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

    # LLM description for top-level modules (not sub-folder features)
    description_section = ""
    if not matched_as_feature and state._modules_result:
        try:
            description = explain_module(actual_name, info, module_graph)
            description_section = f"\n### Description\n{description}\n"
        except Exception as e:
            _log(f"[codewalk_get_module_info] explain_module failed: {e}")

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
    2. LLM-generated line-by-line explanation
    3. Blast radius — which files break if this symbol changes

    Args:
        function_name: Exact name of the function, method, or class,
                       e.g. "scan_directory", "VectorStore", "embed_chunks"
    """
    state.ensure_initialized()
    if state._store is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

    _log(f"[codewalk_explain_function] Looking up: {function_name}")
    results = state._store.search(function_name, n_results=10)
    matches = [
        r for r in results
        if function_name.lower() in r["metadata"].get("symbol_name", "").lower()
    ]

    to_show = matches[:3] if matches else results[:3] if results else []
    if not to_show:
        return f"Function '{function_name}' not found in the codebase."

    context = format_context(to_show)

    # LLM explanation
    source_code = to_show[0]["text"]
    symbol = to_show[0]["metadata"].get("symbol_name", function_name)
    language = to_show[0]["metadata"].get("language", "")
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import StrOutputParser
        llm = get_llm(temperature=0)
        prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a senior engineer explaining code to a new team member. "
                "Given a code snippet, explain what each important section does in plain English. "
                "Be concise — one sentence per logical block. "
                "Do NOT repeat the code, just explain it."
            )),
            ("human", "Explain this {language} code for `{symbol}`:\n\n```{language}\n{code}\n```"),
        ])
        chain = prompt | llm | StrOutputParser()
        explanation = chain.invoke({"symbol": symbol, "code": source_code, "language": language})
        context += f"\n\n### Explanation\n{explanation}"
    except Exception as e:
        _log(f"[codewalk_explain_function] LLM explanation failed: {e}")

    # Blast radius (uses cached graph)
    file_path = to_show[0]["metadata"].get("file_path", "")
    if file_path and state._deps:
        radius = get_blast_radius(file_path, state._deps["graph"])
        risk = radius["risk_level"].upper()
        affected = radius["affected_files"]
        direct_names = [f.split("/")[-1] for f in radius["direct"]]
        transitive_names = [f.split("/")[-1] for f in radius["transitive"]]
        breaks = f"Direct: {', '.join(direct_names)}" if direct_names else "No direct dependents"
        if transitive_names:
            breaks += f" | Transitive: {', '.join(transitive_names)}"
        context += (
            f"\n\n### Blast Radius\n"
            f"**Risk:** {risk} — {affected} files affected\n"
            f"**{breaks}**"
        )

    return context

# ─── TOOL 5 [QUERY · user+AI]: codewalk_get_overview ─────────────────
@mcp.tool()
def codewalk_get_overview() -> str:
    """Get the project overview from Codewalk's computed analysis.

    Returns:
    - Tech stack detection results
    - Module list with file counts
    - Mermaid dependency diagram (auto-generated from dependency graph)
    - Top 10 riskiest files by blast radius with break chains
    """
    state.ensure_initialized()
    if state._modules_result is None or state._repo_path is None or state._deps is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

    _log("[codewalk_get_overview] Generating overview...")
    tech = detect_tech_stack(state._repo_path)
    diagram = generate_module_diagram(state._modules_result["module_graph"])
    modules = list(state._modules_result["modules"].keys())

    blast_map = calculate_full_blast_map(state._deps["graph"])
    top10 = blast_map["blast_map"][:10]

    risky_lines = []
    for item in top10:
        file_path = item["file"]
        name = file_path.split("/")[-1]
        risk = item["risk_level"].upper()
        affected = item["affected_files"]
        radius = get_blast_radius(file_path, state._deps["graph"])
        direct = [f.split("/")[-1] for f in radius["direct"]]
        risky_lines.append(
            f"  [{risk}] {name} — {affected} affected | breaks: {', '.join(direct)}"
        )

    risky_section = "\n".join(risky_lines) if risky_lines else "  No high-risk files"

    # Generate rich overview via LLM (same as API endpoint)
    overview_text = generate_overview(tech, state._modules_result, diagram)

    return (
        f"{overview_text}\n\n"
        f"---\n\n"
        f"### Riskiest Files (blast radius)\n{risky_section}"
    )

# ─── TOOL 6 [QUERY · user+AI]: codewalk_get_blast_radius_map ─────────
@mcp.tool()
def codewalk_get_blast_radius_map(target: str = "") -> str:
    """Get the blast radius (change risk) for files in the codebase.

    Shows which files would break if you change each file.
    Use this when the user asks about risk, impact, or "what breaks if I change X".

    Args:
        target: A module name (e.g. "analysis"), a file name (e.g. "scanner.py"),
                or empty for the top 15 riskiest files across the whole repo.
    """
    state.ensure_initialized()
    if state._modules_result is None or state._repo_path is None or state._deps is None:
        return "Error: No codebase indexed yet. Call codewalk_analyze_codebase first."

    _log(f"[codewalk_get_blast_radius_map] Target: {target or 'top 15'}")
    graph = state._deps["graph"]

    # Determine which files to analyze based on target
    if target:
        # Try module match first
        modules = state._modules_result.get("modules", {})
        actual_module = None
        for name in modules:
            if name.lower() == target.lower():
                actual_module = name
                break

        if actual_module:
            target_files = sorted(modules[actual_module]["files"])
            scope = f"module '{actual_module}'"
        else:
            # Try file name match
            matched = [f for f in graph.keys() if f.split("/")[-1] == target or f.endswith(target)]
            if matched:
                target_files = sorted(matched)
                scope = f"file '{target}'"
            else:
                available_modules = ", ".join(sorted(modules.keys()))
                return (
                    f"'{target}' not found as a module or file.\n"
                    f"Available modules: {available_modules}\n"
                    f"Tip: use the exact file name like 'scanner.py' or module name like 'ingestion'."
                )
    else:
        target_files = sorted(graph.keys())
        scope = "top 15 riskiest"

    risk_order = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "none": 0}
    max_risk = "low"
    results = []

    for file_path in target_files:
        radius = get_blast_radius(file_path, graph)
        if risk_order.get(radius["risk_level"], 0) > risk_order.get(max_risk, 0):
            max_risk = radius["risk_level"]
        results.append((file_path, radius))

    results.sort(key=lambda x: x[1]["affected_files"], reverse=True)

    # If no target specified, show only top 15 non-SAFE files
    if not target:
        results = [r for r in results if r[1]["affected_files"] > 0][:15]

    lines = []
    for file_path, radius in results:
        risk = radius["risk_level"].upper()
        affected = radius["affected_files"]
        short_path = "/".join(file_path.split("/")[-2:])
        if affected > 0:
            direct = [f.split("/")[-1] for f in radius["direct"]]
            transitive = [f.split("/")[-1] for f in radius["transitive"]]
            breaks = f"breaks: {', '.join(direct)}"
            if transitive:
                breaks += f" → then: {', '.join(transitive)}"
            lines.append(f"  [{risk}] {short_path} — {affected} affected | {breaks}")
        else:
            lines.append(f"  [SAFE] {short_path} — no dependents")

    header = (
        f"## Blast Radius — {scope}\n"
        f"**Overall risk:** {max_risk.upper()}\n"
        f"**Files shown:** {len(lines)}\n"
    )

    return header + "\n" + "\n".join(lines)

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
    order = generate_reading_order_raw(state._files, state._deps)
    graph = state._deps["graph"]

    all_items = order["order"]

    # Filter by module if specified
    scope = "entire repo"
    if module_name:
        modules = state._modules_result.get("modules", {})
        actual_name = None
        for name in modules:
            if name.lower() == module_name.lower():
                actual_name = name
                break
        if actual_name is None:
            available = ", ".join(sorted(modules.keys()))
            return f"Module '{module_name}' not found. Available: {available}"
        module_files = set(modules[actual_name]["files"])
        all_items = [item for item in all_items if item["file"] in module_files]
        scope = f"module '{actual_name}'"

    lines = []
    for item in all_items:
        radius = get_blast_radius(item["file"], graph)
        risk = radius["risk_level"].upper()
        pos = item["position"]
        why = item["why"]
        affected = radius["affected_files"]
        lines.append(f"{pos}. [{risk}] {item['file']} ({affected} affected) — {why}")

    header = f"## Reading Order — {scope} ({len(all_items)} files)"

    return header + "\n" + "\n".join(lines)


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

    module_graph = state._modules_result.get("module_graph", {})
    modules = state._modules_result.get("modules", {})

    if not module_name:
        # Module-to-module flow — structured data for Copilot to narrate
        depended_on = set()
        for deps in module_graph.values():
            depended_on.update(deps)
        entry_modules = sorted(m for m in module_graph if m not in depended_on)

        lines = []
        for mod_name in sorted(module_graph.keys()):
            deps = module_graph.get(mod_name, [])
            file_count = modules[mod_name]["file_count"] if mod_name in modules else "?"
            if deps:
                lines.append(f"  {mod_name} ({file_count} files) → depends on: {', '.join(deps)}")
            else:
                lines.append(f"  {mod_name} ({file_count} files) → (standalone)")

        return (
            f"## Execution Flow — Module Level\n"
            f"**Entry modules** (nothing depends on these): {', '.join(entry_modules) or 'None'}\n"
            f"**Total modules:** {len(module_graph)}\n\n"
            f"### Module Dependencies\n"
            + "\n".join(lines)
        )
    else:
        # File-to-file flow within a module
        actual_name = None
        for name in modules:
            if name.lower() == module_name.lower():
                actual_name = name
                break
        if actual_name is None:
            available = ", ".join(sorted(modules.keys()))
            return f"Module '{module_name}' not found. Available: {available}"

        graph = state._deps["graph"]
        internal_files = set(graph.keys())
        module_file_set = set(modules[actual_name]["files"])
        target_files = sorted(f for f in graph.keys() if f in module_file_set)

        imported_in_module = set()
        for fp in target_files:
            for dep in graph.get(fp, []):
                if dep in module_file_set:
                    imported_in_module.add(dep)
        entry_files = [f for f in target_files if f not in imported_in_module]

        dep_lines = []
        for file_path in target_files:
            internal_deps = [d for d in graph.get(file_path, []) if d in internal_files]
            in_module = [d for d in internal_deps if d in module_file_set]
            cross_module = [d for d in internal_deps if d not in module_file_set]
            parts = []
            if in_module:
                parts.append(f"imports: {', '.join(d.split('/')[-1] for d in in_module)}")
            if cross_module:
                parts.append(f"external: {', '.join(d.split('/')[-1] for d in cross_module)}")
            if parts:
                dep_lines.append(f"  {file_path.split('/')[-1]} → {' | '.join(parts)}")
            else:
                dep_lines.append(f"  {file_path.split('/')[-1]} → (no internal imports)")

        entry_names = [f.split("/")[-1] for f in entry_files]

        return (
            f"## Execution Flow — {actual_name} (file level)\n"
            f"**Entry files** (nothing in this module imports these): {', '.join(entry_names)}\n"
            f"**Files:** {len(target_files)}\n\n"
            f"### File Dependencies\n"
            + "\n".join(dep_lines)
        )


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
            f"  - codewalk_get_overview (if LLM didn't call — run manually for project summary)\n"
            f"  - codewalk_search_codebase (if LLM didn't call — search code by concept)\n"
            f"  - codewalk_get_module_info (if LLM didn't call — inspect a specific module)\n"
            f"  - codewalk_explain_function (if LLM didn't call — explain any function/class)\n"
            f"  - codewalk_get_blast_radius_map (if LLM didn't call — check change risk)\n"
            f"  - codewalk_get_reading_order (if LLM didn't call — optimal file reading order)\n"
            f"  - codewalk_get_execution_flow (if LLM didn't call — dependency flow diagram)"
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
    """Run Codewalk's multi-stage code review pipeline on the current git diff.

    Runs 5 automated checks:
      1. Test coverage check — flags source files missing test updates
      2. Blast radius analysis — warns about high-risk files (from dependency graph)
      3. Codebase pattern matching — finds similar code via ChromaDB embeddings
      4. Team guidelines RAG — injects coding standards from indexed guidelines
      5. LLM deep review — OWASP security, bugs, logic errors, style

    Output: markdown report with issues sorted by severity:
    🔴 CRITICAL → 🟡 WARNING → 🟢 SUGGESTION.

    Args:
        staged: If True, review only staged changes (--staged). Default: all unstaged.
        target_branch: Diff against a branch (e.g. "main" for full PR review).
    """
    result = review_diff(
        staged=staged,
        target_branch=target_branch,
        use_llm=True,
        store=state._store,    # cached vector store (if indexed)
        deps=state._deps, 
    )

    if not result.issues:
        return (
            f"✅ No issues found.\n"
            f"Reviewed {result.files_reviewed} files "
            f"(+{result.lines_added} / -{result.lines_removed})\n\n"
            f"{result.summary}"
        )
    
    lines = [
        f"## Code Review — {result.files_reviewed} files "
        f"(+{result.lines_added} / -{result.lines_removed})\n"
    ]

    severity_icons = {"critical": "🔴", "warning": "🟡", "suggestion": "🟢"}

    for issue in sorted(result.issues, key=lambda issue: issue.severity.value):
        icon = severity_icons.get(issue.severity.value, "⚪")
        loc = f"{issue.file_path}:{issue.line_number}" if issue.line_number else issue.file_path
        lines.append(f"{icon} **{issue.title}**")
        lines.append(f"   {loc}")
        lines.append(f"   {issue.explanation}")
        if issue.suggestion:
            lines.append(f"   💡 {issue.suggestion}")
        if issue.code_snippet:
            lines.append(f"   ```\n   {issue.code_snippet}\n   ```")
        lines.append("")

    lines.append(f"\n**Summary:** {result.summary}")
    return "\n".join(lines)

# ─── TOOL 15 [MAINT · user+AI]: codewalk_review_file ────────────
@mcp.tool()
def codewalk_review_file(file_path: str) -> str:
    """Review a file against codebase patterns found via ChromaDB embeddings.

    Uses the vector index to find how similar code is written elsewhere in
    the project, then compares for consistency.

    Checks: consistency with codebase conventions, error handling patterns,
    naming conventions, and potential bugs.

    Requires: codebase must be indexed first via codewalk_index_filtered_files.

    Args:
        file_path: Path to the file to review (relative to repo root).
    """
    state.ensure_initialized()
    if not state._store:
        return "❌ Codebase not indexed. Run codewalk_index_filtered_files first."
    
    from src.codewalk.rag.chain import format_context
    from src.codewalk.config import get_llm

    try:
        with open(file_path, "r") as file:
            content = file.read()
    except FileNotFoundError:
        return f"❌ File '{file_path}' not found."
    
    results = state._store.search(f"code in {file_path}", n_results=5)
    patterns = format_context(results) if results else "No indexed context."

    llm = get_llm(temperature=0)
    response = llm.invoke([
        {"role": "system", "content": (
            "You are a senior engineer reviewing a file against codebase conventions.\n\n"
            "Given: the file's source code + patterns found elsewhere in the codebase.\n\n"
            "SEVERITY LEVELS:\n"
            "- 🔴 CRITICAL: Bugs, security vulnerabilities\n"
            "- 🟡 WARNING: Logic errors, inconsistency with codebase patterns\n"
            "- 🟢 SUGGESTION: Readability, naming improvements\n\n"
            "RULES:\n"
            "- Only reference line numbers you can see in the provided code\n"
            "- Compare against the 'Patterns elsewhere' section for consistency\n"
            "- If the file follows conventions well, say so (don't invent issues)\n"
            "- If the file was truncated, only review what you can see\n\n"
            "Format: One issue per line as '🔴/🟡/🟢 Line X: [issue] — [suggestion]'\n"
            "End with a one-line summary."
        )},
        {"role": "user", "content": (
            f"## File: {file_path}\n```\n{content[:10000]}\n```"
            f"{chr(10) + '(File truncated at 10000 chars)' if len(content) > 10000 else ''}\n\n"
            f"## Patterns elsewhere:\n{patterns}"
        )},
    ])

    return response.content

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