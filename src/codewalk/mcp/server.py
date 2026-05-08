from mcp.server.fastmcp import FastMCP

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.generation.overview_generator import generate_overview
from src.codewalk.generation.module_explainer import explain_module
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context
from src.codewalk.pipeline import full_index
from src.codewalk.analysis.reading_order import generate_reading_order
from src.codewalk.generation.flow_generator import generate_execution_flow
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
def analyze_codebase(repo_path: str) -> str:
    """Index a codebase for searching and analysis.

    This must be called FIRST before any other tool.
    Scans, chunks, embeds, and stores the codebase in a vector database.

    Args:
        repo_path: Path to the repository root, e.g. "." or "/path/to/repo"
    """
    global _store, _modules_result, _repo_path

    _store = VectorStore()
    _store.create_collection("codebase")
    existing = _store.collection.count()

    if existing > 0:
        indexed_results = {
            "files_scanned": 0,
            "chunks_created": 0,
            "skipped": True,
        }
        print(f"Skipping indexing — {existing} chunks already in DB")
    else:
        indexed_results = full_index(repo_path)
        _store = VectorStore()
        _store.create_collection("codebase")

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

    return (
        f"## Module: {actual_name}\n"
        f"**Files ({info['file_count']}):** {', '.join(file_names)}\n"
        f"**Languages:** {lang_str}\n"
        f"**Depends on:** {', '.join(depends_on) or 'None (standalone)'}\n"
        f"**Depended on by:** {', '.join(depended_by) or 'None'}"
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

    if matches:
        return format_context(matches[:3])
    if results:
        return format_context(results[:3])
    return f"Function '{function_name}' not found in the codebase."

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

    return (
        f"## Project Overview\n"
        f"**Tech stack:** {', '.join(tech) if tech else 'Not detected'}\n"
        f"**Files:** {_modules_result['stats']['total_files']}\n"
        f"**Modules ({len(modules)}):** {', '.join(modules)}\n\n"
        f"### Dependency Diagram\n```mermaid\n{diagram}\n```"
    )

# ─── TOOL 6: get_reading_order ───────────────────────────────────────────
@mcp.tool()
def get_reading_order() -> str:
    """Get the recommended reading order for the codebase."""
    if _modules_result is None or _repo_path is None:
        return "Error: No codebase indexed yet."
    
    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    order = generate_reading_order(files, deps)
    lines = [f"{item["position"]}. {item["file"].split("/")[-1]} — {item["why"]}"
             for item in order["order"]]
    
    return "## Reading Order\n" + "\n".join(lines)


# ─── TOOL 7: get_execution_flow ───────────────────────────────────────────
@mcp.tool()
def get_execution_flow() -> str:
    """Get the execution flow diagram and narration."""
    if _modules_result is None or _repo_path is None:
        return "Error: No codebase indexed yet."
    
    files = scan_directory(_repo_path)
    deps = build_dependency_graph(files)
    order = generate_reading_order(files, deps)

    return generate_execution_flow(order, deps)





if __name__ == "__main__":
    mcp.run(transport="stdio")