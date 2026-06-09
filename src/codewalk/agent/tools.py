from langchain_core.tools import tool

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.graph.graph_runtime import GraphRuntime
from src.codewalk.query import (
    module_info_text, explain_function_text,
    overview_text, blast_radius_map_text, reading_order_text,
    execution_flow_text,
)
from src.codewalk.rag.chain import ask_corrective
from src.codewalk.review.reviewer import review_diff as _review_diff
from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
from src.codewalk.config import settings
from src.codewalk.review.fix_applier import apply_fix_to_file

def create_tools(store: VectorStore, modules_result: dict,
                 files: list[dict] = None, deps: dict = None,
                 graph_runtime: GraphRuntime | None = None,
                 graph_store: GraphStore | None = None) -> list:
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
        """Search the codebase and generate a verified answer.

        Uses corrective RAG: retrieves code chunks, grades them for
        relevance, generates an answer, and verifies it is faithful
        to the retrieved code. Automatically retries with query
        rewriting if the first attempt fails.

        Falls back to graph-neighbor expansion when semantic search
        alone is not enough.

        Use this for ANY question about code, functions, features, or
        implementation details.

        Args:
            query: Natural language question, e.g. "how does authentication work"
        """
        result = ask_corrective(query, store, graph_store=graph_store)
        meta = (
            f"\n\n---\n_Confident: {result['confident']} | "
            f"Retries: {result['retries']} | "
            f"Chunks: {result['relevant_chunks']} | "
            f"Confidence: {result['retrieval_confidence']:.2f}_"
        )
        return result["answer"] + meta

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
        return module_info_text(modules_result, module_name, graph_runtime, graph_store)

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
        return explain_function_text(store, function_name, deps, graph_runtime, graph_store)

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
            graph_store=graph_store,
            repo_path=settings.repo_path,
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

    # ─── TOOL 9: review_file ────────────────────────────────────
    @tool
    def review_file(file_path: str) -> str:
        """Review a single file for bugs, security issues, and code quality.

        Works on any file — doesn't need to be in git diff.
        Returns the file with context (imports, callers, guidelines)
        for analysis.

        Args:
            file_path: Path to the file to review (relative to repo root).
        """
        import os
        from src.codewalk.review.reviewer import (
            _get_caller_context, _get_security_context_for_file,
        )
        from src.codewalk.review.models import DiffFile, DiffHunk, ChangedLine
        from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
        from src.codewalk.rag.chain import format_context as _format_context

        repo_path = settings.repo_path
        full_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

        if not os.path.exists(full_path):
            return f"File '{file_path}' not found."

        try:
            content = open(full_path, "r", errors="replace").read()
        except OSError as e:
            return f"Cannot read file: {e}"

        file_lines = content.splitlines()
        changed_lines = [
            ChangedLine(line_number=i + 1, content=line, change_type="added")
            for i, line in enumerate(file_lines)
        ]
        synthetic_diff = DiffFile(
            file_path=file_path, language="",
            hunks=[DiffHunk(start_line=1, end_line=len(file_lines), lines=changed_lines)],
            is_new_file=True, added_lines=len(file_lines), removed_lines=0,
        )

        output_parts = [f"## File Review: {file_path} ({len(file_lines)} lines)\n"]

        caller_ctx = _get_caller_context(synthetic_diff, deps, graph_store)
        if caller_ctx:
            output_parts.append(caller_ctx)

        if store:
            sec_ctx = _get_security_context_for_file(synthetic_diff, store)
            if sec_ctx:
                output_parts.append(sec_ctx)
            from src.codewalk.rag.chain import retrieve_corrective as _retrieve_corrective
            result = _retrieve_corrective(
                f"code in {file_path}", store,
                graph_store=graph_store,
            )
            if result["chunks"]:
                output_parts.append("## Similar patterns elsewhere")
                output_parts.append(_format_context(result["chunks"]))

        guidelines_store = get_guidelines_store()
        if guidelines_store:
            gl = search_guidelines(guidelines_store, [synthetic_diff], n_results=3)
            if gl:
                output_parts.append(gl)

        truncated = content[:15000]
        if len(content) > 15000:
            truncated += "\n... (truncated at 15000 chars)"
        output_parts.append(f"<file>\n{truncated}\n</file>")

        return "\n\n".join(output_parts)

    # ─── TOOL 10: load_guidelines ────────────────────────────────
    @tool
    def load_guidelines(docs_path: str = "") -> str:
        """Load team coding guidelines for use in code reviews.

        Reads guideline documents (.md, .txt, .rst) from the given directory,
        embeds them, and makes them available to review_diff and review_file.

        Args:
            docs_path: Path to directory containing guideline files.
                       Falls back to REVIEW_GUIDELINES_PATH env var.
        """
        import os

        path = docs_path or settings.review_guidelines_path
        if not path:
            return (
                "No path provided. Either pass docs_path or set "
                "REVIEW_GUIDELINES_PATH in your .env file."
            )

        if not os.path.isdir(path):
            return f"Directory not found: {path}"

        gl_store = get_guidelines_store()
        if not gl_store:
            return f"No guideline files found in {path}"

        count = gl_store.chunk_count()
        return f"Loaded {count} guideline chunks from {path}"
    

    # ─── TOOL 11: get_architecture_health ─────────────────────────
    @tool
    def get_architecture_health() -> str:
        """Architecture health report: bottlenecks, key files, circular dependencies.

        Returns graph stats, betweenness centrality (bottleneck files),
        PageRank (most important files), and cycle detection with fixes.
        Use when asked about architecture, code health, or refactoring priorities.
        """
        if graph_runtime is None:
            return "Error: No graph data available."
        
        stats = graph_runtime.get_graph_stats()
        centrality = graph_runtime.centrality(top_n=5)
        cycles = graph_runtime.detect_cycles()

        parts = [
            f"Files: {stats['file_graph']['vertices']}, "
            f"Edges: {stats['file_graph']['edges']}, "
            f"DAG: {'Yes' if stats['file_graph']['is_dag'] else 'No'}",
        ]

        if centrality["betweenness"]:
            top = [f"{item['file'].rsplit('/', 1)[-1]} ({item['score']})"
                   for item in centrality["betweenness"] if item["score"] > 0]
            if top:
                parts.append(f"Bottlenecks: {', '.join(top[:5])}")
        
        if centrality["pagerank"]:
            top_pr = [f"{item['file'].rsplit('/', 1)[-1]}"
                      for item in centrality["pagerank"][:5]]
            parts.append(f"Key files (PageRank): {', '.join(top_pr)}")

        if cycles["has_cycles"]:
            parts.append(f"Cycles: {len(cycles['cycle_groups'])} groups found")
            for i, group in enumerate(cycles["cycle_groups"], 1):
                names = [f.rsplit('/', 1)[-1] for f in group]
                parts.append(f"  Cycle {i}: {' ↔ '.join(names)}")
            if cycles["edges_to_break"]:
                parts.append("Fix — remove these imports:")
                for s, t in cycles["edges_to_break"]:
                    parts.append(f"  - {s.rsplit('/', 1)[-1]} → {t.rsplit('/', 1)[-1]}")
        else:
            parts.append("Cycles: None (clean DAG)")
    
        return "\n".join(parts)

    # ─── TOOL 12: apply_fix ──────────────────────────────────────
    @tool
    def apply_fix(file_path: str, old_code: str, new_code: str) -> str:
        """Apply a code fix by replacing old_code with new_code in the file.

        This tool ACTUALLY EDITS FILES ON DISK. It will be interrupted by HITL
        before execution, so the user must approve each fix via /chat/approve.

        Args:
            file_path: Relative path to the file (e.g. "src/auth/login.py")
            old_code:  The EXACT code to search for (must match precisely)
            new_code:  The replacement code
        """
        result = apply_fix_to_file(settings.repo_path, file_path, old_code, new_code)
        if result["ok"]:
            return result["message"]
        return f"Error: {result['error']}"

    return [search_codebase, get_module_info, explain_function,
            get_overview, get_blast_radius_map, get_reading_order,
            get_execution_flow, review_diff, review_file, load_guidelines,
            get_architecture_health, apply_fix]





