from langchain_core.tools import tool

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.query import (
    search_codebase_text, module_info_text, explain_function_text,
    overview_text, blast_radius_map_text, reading_order_text,
    execution_flow_text,
)
from src.codewalk.review.reviewer import review_diff as _review_diff
from src.codewalk.config import settings

def create_tools(store: VectorStore, modules_result: dict,
                 files: list[dict] = None, deps: dict = None,
                 graph_runtime=None) -> list:
    """Build agent tools with access to the indexed codebase data.

    Args:
        store: VectorStore with an active collection (already indexed).
        modules_result: Full result dict from detect_modules().
                        Has "modules", "module_graph", "source_root", "stats".
        files: scan_directory() result (for reading order).
        deps: build_dependency_graph() result (for blast radius).
        graph_runtime: Optional GraphRuntime for igraph fast path.

    Returns:
        List of tool functions the agent can call.
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
        return search_codebase_text(store, query)

    # ─── TOOL 2: get_module_info ─────────────────────────────────
    @tool
    def get_module_info(module_name: str) -> str:
        """Get detailed information about a specific module in the codebase.

        Use this tool when the user asks about a module's purpose, files,
        dependencies, or structure. Returns module details including file
        list, languages, and dependency relationships.

        Args:
            module_name: Name of the module, e.g. "analysis", "rag", "ingestion"
        """
        return module_info_text(modules_result, module_name)

    # ─── TOOL 3: explain_function ────────────────────────────────
    @tool
    def explain_function(function_name: str) -> str:
        """Find a specific function or class by name and return its source code.

        Use this tool when the user asks about a specific function, method,
        or class by name. Returns the source code with file location and
        blast radius (what breaks if this code changes).

        Args:
            function_name: Name of the function or class, e.g. "scan_directory"
        """
        return explain_function_text(store, function_name, deps, graph_runtime)

    # ─── TOOL 4: get_overview ────────────────────────────────────
    @tool
    def get_overview() -> str:
        """Get a high-level overview of the analyzed codebase.

        Returns tech stack, module list, dependency flow, entry/core modules,
        and riskiest files. Use when user asks "what is this project" or
        "give me an overview".
        """
        if deps is None:
            return "Error: No analysis data available."
        return overview_text(settings.repo_path, modules_result, deps, graph_runtime)

    # ─── TOOL 5: get_blast_radius_map ────────────────────────────
    @tool
    def get_blast_radius_map(target: str = "") -> str:
        """Get the blast radius (change risk) for files in the codebase.

        Shows which files would break if you change each file.
        Use when user asks about risk, impact, or "what breaks if I change X".

        Args:
            target: A module name (e.g. "analysis"), a file name (e.g. "scanner.py"),
                    or empty for the top 30 riskiest files.
        """
        if deps is None:
            return "Error: No analysis data available."
        return blast_radius_map_text(modules_result, deps, target, graph_runtime)

    # ─── TOOL 6: get_reading_order ───────────────────────────────
    @tool
    def get_reading_order(module_name: str = "") -> str:
        """Get the recommended reading order for the codebase.

        Returns files in dependency order (read dependencies first).
        Each file shows position, dependency info, and blast radius risk.

        Args:
            module_name: Optional. Scope to a specific module, e.g. "analysis".
                         If empty, returns order for the entire repo.
        """
        if files is None or deps is None:
            return "Error: No analysis data available."
        return reading_order_text(files, deps, modules_result, module_name, graph_runtime)

    # ─── TOOL 7: get_execution_flow ──────────────────────────────
    @tool
    def get_execution_flow(module_name: str = "") -> str:
        """Get the execution flow showing how code connects.

        Without module_name: returns module-to-module flow plus entry modules.
        With module_name: returns file-to-file flow within that module.

        Args:
            module_name: Optional. Show file-level flow inside this module.
                         If empty, shows module-level flow for the whole repo.
        """
        if deps is None:
            return "Error: No analysis data available."
        return execution_flow_text(modules_result, deps, module_name)

    # ─── TOOL 8: review_diff ─────────────────────────────────────
    @tool
    def review_diff(staged: bool = False, target_branch: str = "") -> str:
        """Review git diff for bugs, security issues, and style problems.

        Runs multi-stage review: test coverage check, blast radius,
        codebase patterns, team guidelines, and LLM deep review.

        Args:
            staged: If True, review only staged changes. Default: unstaged.
            target_branch: Diff against a branch (e.g. "main") for full PR review.
        """
        result = _review_diff(
            staged=staged,
            target_branch=target_branch or None,
            use_llm=True,
            store=store,
            deps=deps,
        )

        if not result.issues:
            return (
                f"✅ No issues found.\n"
                f"Reviewed {result.files_reviewed} files "
                f"(+{result.lines_added} / -{result.lines_removed})\n\n"
                f"{result.summary}"
            )

        lines = []
        for issue in result.issues:
            icon = {"critical": "🔴", "warning": "🟡", "suggestion": "🟢"}.get(
                issue.severity, "⚪"
            )
            loc = f"{issue.file_path}:{issue.line_number}" if issue.line_number else issue.file_path
            lines.append(f"{icon} [{issue.category}] {loc}: {issue.title}")
            if issue.explanation:
                lines.append(f"   {issue.explanation}")

        return (
            f"## Code Review Results\n"
            f"Reviewed {result.files_reviewed} files "
            f"(+{result.lines_added} / -{result.lines_removed})\n\n"
            + "\n".join(lines)
            + f"\n\n**Summary:** {result.summary}"
        )

    return [search_codebase, get_module_info, explain_function,
            get_overview, get_blast_radius_map, get_reading_order,
            get_execution_flow, review_diff]



