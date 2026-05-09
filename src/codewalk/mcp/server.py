from mcp.server.fastmcp import FastMCP

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.generation.module_explainer import explain_module
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context
from src.codewalk.pipeline import full_index, reindex
from src.codewalk.config import settings
from src.codewalk.analysis.blast_radius import (
    get_blast_radius,
    calculate_full_blast_map,
)
from src.codewalk.analysis.reading_order import generate_reading_order_raw


# ─── Create the MCP server ──────────────────────────────────────────
mcp = FastMCP(
    name="codewalk",
    instructions=(
        "Codewalk is an AI-powered codebase onboarding tool. "
        "Use these tools to analyze, search, and explain code in a repository. "
        "Always call analyze_codebase first before using other tools."
    ),
)

_store: VectorStore | None = None
_modules_result: dict | None = None
_repo_path: str | None = None


# ─── TOOL 1: analyze_codebase ───────────────────────────────────────
@mcp.tool()
def analyze_codebase(index_mode: str = "auto") -> str:
    """Index a codebase for searching and analysis.

    This must be called FIRST before any other tool.
    Scans, chunks, embeds, and stores the codebase in a vector database.

    Args:
        index_mode: Indexing mode.
              "auto" — skip if already indexed (default)
              "reindex" — re-index only changed/new/deleted files
              "full" — delete everything and re-embed from scratch
    """
    global _store, _modules_result, _repo_path

    repo_path = settings.repo_path
    _store = VectorStore()
    _store.create_collection("codebase")
    existing = _store.collection.count()

    if index_mode == "full" or existing == 0:
        indexed_results = full_index(repo_path)
        # Re-open store after full index (collection was recreated)
        _store = VectorStore()
        _store.create_collection("codebase")
    elif index_mode == "reindex":
        indexed_results = reindex(repo_path)
        _store = VectorStore()
        _store.create_collection("codebase")
    else:
        # Auto mode + data exists → skip
        indexed_results = {
            "files_scanned": 0,
            "chunks_created": 0,
            "skipped": True,
        }
        print(f"Skipping indexing — {existing} chunks already in DB")

    files = scan_directory(repo_path)
    deps = build_dependency_graph(files)
    _modules_result = detect_modules(files, deps)
    _repo_path = repo_path

    modules = list(_modules_result["modules"].keys())
    return (
        f"Codebase indexed successfully.\n"
        f"Files scanned: {indexed_results['files_scanned']}\n"
        f"Chunks created: {indexed_results['chunks_created']}\n"
        f"Modules found: {', '.join(modules)}"
    )

# ─── TOOL 2: search_codebase ────────────────────────────────────────
@mcp.tool()
def search_codebase(query: str) -> str:
    """Search the indexed codebase for code related to a query.

    Use this when the user asks about specific code, functions,
    files, or implementation details.

    Args:
        query: Natural language search query, e.g. "authentication logic"
    """
    if _store is None:
        return "Error: No codebase indexed yet. Call analyze_codebase first."

    results = _store.search(query, n_results=5)
    if not results:
        return "No relevant code found for that query."
    return format_context(results)

# ─── TOOL 3: get_module_info ────────────────────────────────────────
@mcp.tool()
def get_module_info(module_name: str) -> str:
    """Get information about a specific module in the codebase.

    Returns file list, languages, dependencies, and which modules
    depend on this one.

    Args:
        module_name: Name of the module, e.g. "lib", "src", "tests"
    """
    if _modules_result is None:
        return "Error: No codebase indexed yet. Call analyze_codebase first."

    modules = _modules_result.get("modules", {})
    module_graph = _modules_result.get("module_graph", {})

    # Case-insensitive lookup
    actual_name = None
    for name in modules:
        if name.lower() == module_name.lower():
            actual_name = name
            break

    if actual_name is None:
        available = ", ".join(sorted(modules.keys()))
        return f"Module '{module_name}' not found. Available modules: {available}"

    info = modules[actual_name]
    depends_on = module_graph.get(actual_name, [])
    depended_by = [n for n, deps in module_graph.items() if actual_name in deps]
    file_names = [p.split("/")[-1] for p in sorted(info["files"])]
    lang_str = ", ".join(f"{l} ({c})" for l, c in sorted(info["languages"].items()))

    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    graph = deps["graph"]

    risk_lines = []
    for file_path in sorted(info["files"]):
        radius = get_blast_radius(file_path, graph)
        name = file_path.split("/")[-1]
        risk = radius["risk_level"].upper()
        affected = radius["affected_files"]
        if affected > 0:
            direct = [f.split("/")[-1] for f in radius["direct"]]
            transitive = [f.split("/")[-1] for f in radius["transitive"]]
            breaks = f"breaks: {', '.join(direct)}"
            if transitive:
                breaks += f" → then: {', '.join(transitive)}"
            risk_lines.append(f"  [{risk}] {name} — {affected} affected | {breaks}")
        else:
            risk_lines.append(f"  [SAFE] {name} — no dependents")

    risk_section = "\n".join(risk_lines)

    return (
        f"## Module: {actual_name}\n"
        f"**Files ({info['file_count']}):** {', '.join(file_names)}\n"
        f"**Languages:** {lang_str}\n"
        f"**Depends on:** {', '.join(depends_on) or 'None (standalone)'}\n"
        f"**Depended on by:** {', '.join(depended_by) or 'None'}\n\n"
        f"### Blast Radius (change risk per file)\n{risk_section}"
    )

# ─── TOOL 4: explain_function ───────────────────────────────────────
@mcp.tool()
def explain_function(function_name: str) -> str:
    """Find a specific function or class by name and return its source code.

    Use this when the user asks about a specific function, method,
    or class. Returns source code with file location.

    Args:
        function_name: Name of the function or class, e.g. "scan_directory"
    """
    if _store is None:
        return "Error: No codebase indexed yet. Call analyze_codebase first."

    results = _store.search(function_name, n_results=10)
    matches = [
        r for r in results
        if function_name.lower() in r["metadata"].get("symbol_name", "").lower()
    ]

    to_show = matches[:3] if matches else results[:3] if results else []
    if not to_show:
        return f"Function '{function_name}' not found in the codebase."
    
    context = format_context(to_show)

    file_path = to_show[0]["metadata"].get("file_path", "")
    if file_path and _repo_path:
        all_files = scan_directory(_repo_path)
        deps = build_dependency_graph(all_files)
        radius = get_blast_radius(file_path, deps["graph"])
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

# ─── TOOL 5: get_overview ───────────────────────────────────────────
@mcp.tool()
def get_overview() -> str:
    """Get a high-level overview of the analyzed codebase.

    Returns tech stack, module list, dependency diagram, and
    file/module counts. Call analyze_codebase first.
    """
    if _modules_result is None or _repo_path is None:
        return "Error: No codebase indexed yet. Call analyze_codebase first."

    tech = detect_tech_stack(_repo_path)
    diagram = generate_module_diagram(_modules_result["module_graph"])
    modules = list(_modules_result["modules"].keys())

    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    blast_map = calculate_full_blast_map(deps["graph"])
    top3 = blast_map["blast_map"][:3]

    risky_lines = []
    for item in top3:
        file_path = item["file"]
        name = file_path.split("/")[-1]
        risk = item["risk_level"].upper()
        affected = item["affected_files"]
        radius = get_blast_radius(file_path, deps["graph"])
        direct = [f.split("/")[-1] for f in radius["direct"]]
        risky_lines.append(
            f"  [{risk}] {name} — {affected} affected | breaks: {', '.join(direct)}"
        )

    risky_section = "\n".join(risky_lines) if risky_lines else "  No high-risk files"

    return (
        f"## Project Overview\n"
        f"**Tech stack:** {', '.join(tech) if tech else 'Not detected'}\n"
        f"**Files:** {_modules_result['stats']['total_files']}\n"
        f"**Modules ({len(modules)}):** {', '.join(modules)}\n\n"
        f"### Dependency Diagram\n```mermaid\n{diagram}\n```\n\n"
        f"### Riskiest Files (blast radius)\n{risky_section}"
    )

# ─── TOOL 6: get_blast_radius_map ────────────────────────────────────────
@mcp.tool()
def get_blast_radius_map(module_name: str = "") -> str:
    """Get the blast radius (change risk) for files in the codebase.

    Shows which files would break if you change each file.
    Use this when the user asks about risk, impact, or "what breaks if I change X".

    Args:
        module_name: Optional. Scope to a specific module, e.g. "analysis", "embeddings".
                     If empty, shows all files in the repo.
    """
    if _modules_result is None or _repo_path is None:
        return "Error: No codebase indexed yet. Call analyze_codebase first."

    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    graph = deps["graph"]

    # Determine which files to analyze
    if module_name:
        modules = _modules_result.get("modules", {})
        actual_name = None
        for name in modules:
            if name.lower() == module_name.lower():
                actual_name = name
                break
        if actual_name is None:
            available = ", ".join(sorted(modules.keys()))
            return f"Module '{module_name}' not found. Available modules: {available}"
        target_files = sorted(modules[actual_name]["files"])
        scope = actual_name
    else:
        target_files = sorted(graph.keys())
        scope = "all files"

    risk_order = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "none": 0}
    max_risk = "low"
    results = []

    for file_path in target_files:
        radius = get_blast_radius(file_path, graph)
        if risk_order.get(radius["risk_level"], 0) > risk_order.get(max_risk, 0):
            max_risk = radius["risk_level"]
        results.append((file_path, radius))

    results.sort(key=lambda x: x[1]["affected_files"], reverse=True)

    lines = []
    for file_path, radius in results:
        name = file_path.split("/")[-1]
        risk = radius["risk_level"].upper()
        affected = radius["affected_files"]
        if affected > 0:
            direct = [f.split("/")[-1] for f in radius["direct"]]
            transitive = [f.split("/")[-1] for f in radius["transitive"]]
            breaks = f"breaks: {', '.join(direct)}"
            if transitive:
                breaks += f" \u2192 then: {', '.join(transitive)}"
            lines.append(f"  [{risk}] {name} \u2014 {affected} affected | {breaks}")
        else:
            lines.append(f"  [SAFE] {name} \u2014 no dependents")

    return (
        f"## Blast Radius \u2014 {scope}\n"
        f"**Overall risk:** {max_risk.upper()}\n"
        f"**Files:** {len(results)}\n\n"
        + "\n".join(lines)
    )

# ─── TOOL 7: get_reading_order ───────────────────────────────────────────
@mcp.tool()
def get_reading_order() -> str:
    """Get the recommended reading order for the codebase.

    Returns files in dependency order (read dependencies first).
    Each file shows its position, dependency info, and blast radius risk.
    Use this to guide a developer through the codebase.
    """
    if _modules_result is None or _repo_path is None:
        return "Error: No codebase indexed yet."

    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    order = generate_reading_order_raw(files, deps)  # <-- no LLM
    graph = deps["graph"]

    lines = []
    for item in order["order"]:
        radius = get_blast_radius(item["file"], graph)
        risk = radius["risk_level"].upper()
        name = item["file"].split("/")[-1]
        pos = item["position"]
        why = item["why"]
        affected = radius["affected_files"]
        lines.append(f"{pos}. [{risk}] {name} ({affected} affected) — {why}")

    return "## Reading Order\n" + "\n".join(lines)


# ─── TOOL 8: get_execution_flow ───────────────────────────────────────────
@mcp.tool()
def get_execution_flow() -> str:
    """Get the execution flow data for the codebase.

    Returns file dependency graph showing what imports what,
    entry points, and import chains. Use this to understand
    how the code executes from start to finish.
    """
    if _modules_result is None or _repo_path is None:
        return "Error: No codebase indexed yet."

    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    graph = deps["graph"]
    internal_files = set(graph.keys())

    # Find entry points (files nothing imports)
    imported_files = set()
    for file_deps in graph.values():
        for dep in file_deps:
            if dep in internal_files:
                imported_files.add(dep)

    entry_points = sorted(f for f in internal_files if f not in imported_files)

    # Build dependency summary
    dep_lines = []
    for file_path in sorted(graph.keys()):
        name = file_path.split("/")[-1]
        internal_deps = [d.split("/")[-1] for d in graph[file_path] if d in internal_files]
        if internal_deps:
            dep_lines.append(f"  {name} → imports: {', '.join(internal_deps)}")
        else:
            dep_lines.append(f"  {name} → (no internal imports)")

    entry_names = [e.split("/")[-1] for e in entry_points]

    return (
        f"## Execution Flow Data\n"
        f"**Entry points** (nothing imports these): {', '.join(entry_names)}\n"
        f"**Total files:** {len(internal_files)}\n\n"
        f"### Dependency Graph (what imports what)\n"
        + "\n".join(dep_lines)
    )




if __name__ == "__main__":
    mcp.run(transport="stdio")