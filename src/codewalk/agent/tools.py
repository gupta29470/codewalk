from langchain_core.tools import tool

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context

def create_tools(store: VectorStore, modules_result: dict) -> list:
    """Build agent tools with access to the indexed codebase data.

    Args:
        store: VectorStore with an active collection (already indexed).
        modules_result: Full result dict from detect_modules().
                        Has "modules", "module_graph", "source_root", "stats".

    Returns:
        List of 3 tool functions the agent can call.
    """

    # ─── TOOL 1: search_codebase ─────────────────────────────────
    @tool
    def search_codebase(query: str) -> str:
        """Search the indexed codebase for code related to the query.

        Use this tool when the user asks about specific code, functions,
        files, or implementation details. Returns relevant code snippets
        with file paths and function names.

        Args:
            query: Natural language search query, e.g. "authentication logic"
        """
        results = store.search(query, n_results=5)
        if not results:
            return "No relevant code found for that query."
        return format_context(results)
    
    @tool
    def get_module_info(module_name: str) -> str:
        """Get detailed information about a specific module in the codebase.

        Use this tool when the user asks about a module's purpose, files,
        dependencies, or structure. Returns module details including file
        list, languages, and dependency relationships.

        Args:
            module_name: Name of the module, e.g. "analysis", "rag", "ingestion"
        """
        modules = modules_result.get("modules", {})
        module_graph = modules_result.get("module_graph", {})

        # Try exact match first
        if module_name not in modules:
            # Try case-insensitive match
            for name in modules:
                if name.lower() == module_name.lower():
                    module_name = name
                    break
            else:
                available = ", ".join(sorted(modules.keys()))
                return f"Module '{module_name}' not found. Available modules: {available}"
        
        info = modules[module_name]
        depends_on = module_graph.get(module_name, [])

        # Reverse lookup: who depends on this module?
        depended_by = [
            other for other, deps in module_graph.items()
            if module_name in deps
        ]

        # Format file list (just filenames)
        file_names = [path.split("/")[-1] for path in sorted(info["files"])]

        # Format languages
        lang_str = ", ".join(
            f"{lang} ({count} files)"
            for lang, count in sorted(info["languages"].items())
        )

        lines = [
            f"## Module: {module_name}",
            f"**Files ({info['file_count']}):** {', '.join(file_names)}",
            f"**Languages:** {lang_str}",
            f"**Depends on:** {', '.join(depends_on) if depends_on else 'None (standalone)'}",
            f"**Depended on by:** {', '.join(depended_by) if depended_by else 'None'}",
        ]

        return "\n".join(lines)
    
    # ─── TOOL 3: explain_function ────────────────────────────────
    @tool
    def explain_function(function_name: str) -> str:
        """Find a specific function or class by name and return its source code.

        Use this tool when the user asks about a specific function, method,
        or class by name. Returns the source code with file location.

        Args:
            function_name: Name of the function or class, e.g. "scan_directory"
        """
        # Search ChromaDB — vector similarity finds relevant chunks
        results = store.search(function_name, n_results=10)

        # Filter for exact or partial symbol name match
        matches = []
        for result in results:
            symbol = result["metadata"].get("symbol_name", "")
            if symbol and function_name.lower() in symbol.lower():
                matches.append(result)
            
        if not matches:
            # No symbol match — fall back to top vector search results
            return format_context(results[:3]) if results else \
                f"Function '{function_name}' not found in the codebase."
    
        return format_context(matches[:3])
    
    return [search_codebase, get_module_info, explain_function]



