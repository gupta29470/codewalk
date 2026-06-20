from langchain_core.tools import tool

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.graph.graph_runtime import GraphRuntime
from src.codewalk.query import (
    module_info_text, explain_function_text,
    overview_text, blast_radius_map_text, reading_order_text,
    execution_flow_text,
)
from src.codewalk.rag.chain import ask_corrective, format_context as _format_context
from src.codewalk.rag.chain import retrieve_corrective as _retrieve_corrective
from src.codewalk.review.reviewer import review_diff as _review_diff
from src.codewalk.review.reviewer import _get_caller_context, _get_security_context_for_file
from src.codewalk.review.models import DiffFile, DiffHunk, ChangedLine
from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
from src.codewalk.config import settings
from src.codewalk.review.fix_applier import apply_fix_to_file
from src.codewalk.tools.static_analysis import run_static_analysis
from src.codewalk.tools.test_runner import run_tests

# Tools that mutate the repo — API HITL interrupts only before these run.
WRITE_TOOL_NAMES = frozenset({"apply_fix"})


def format_write_tool_calls(tool_calls: list) -> str:
    """Format pending write tool calls for HITL approval UI."""
    writes = [tc for tc in tool_calls if tc.get("name") in WRITE_TOOL_NAMES]
    return "\n".join(
        f"• {tc.get('name', '?')}: {tc.get('args', {})}"
        for tc in writes
    )

def create_tools(
    store: VectorStore,
    modules_result: dict,
    files: list[dict] | None = None,
    deps: dict | None = None,
    graph_runtime: GraphRuntime | None = None,
    graph_store: GraphStore | None = None,
    repo_path: str | None = None,
) -> list:
    """Build agent tools with access to the indexed codebase data.

    Args:
        store: VectorStore with an active collection (already indexed).
        modules_result: Full result dict from detect_modules().
                        Has "modules", "module_graph", "source_root", "stats".
        files: scan_directory() result (for reading order).
        deps: build_dependency_graph() result (for blast radius).
        graph_runtime: Optional GraphRuntime for igraph fast path.
        repo_path: Root of the repository the tools operate on.

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
        confidence = result.get("retrieval_confidence")
        confidence_text = f"{confidence:.2f}" if confidence is not None else "N/A"
        meta = (
            f"\n\n---\n_Confident: {result.get('confident')} | "
            f"Retries: {result.get('retries')} | "
            f"Chunks: {result.get('relevant_chunks')} | "
            f"Confidence: {confidence_text}_"
        )
        return result.get("answer", "") + meta

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
        if not repo_path:
            return "Error: No repo path available."
        return overview_text(repo_path, modules_result, deps, graph_runtime)

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
        if not repo_path:
            return "Error: No repo path available."
        result = _review_diff(
            staged=staged,
            target_branch=target_branch or None,
            use_llm=True,
            store=store,
            deps=deps,
            graph_store=graph_store,
            repo_path=repo_path,
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

        if not repo_path:
            return "Error: No repo path available."
        full_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path
        real_repo = os.path.realpath(repo_path)
        real_full = os.path.realpath(full_path)

        # Path traversal guard: file must be inside the repo
        if real_full != real_repo and not real_full.startswith(real_repo + os.sep):
            return f"Error: '{file_path}' is outside the repository."

        if not os.path.exists(real_full):
            return f"File '{file_path}' not found."
        if not os.path.isfile(real_full):
            return f"Error: '{file_path}' is not a file."

        try:
            with open(real_full, "r", errors="replace") as f:
                content = f.read()
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

        Reads guideline documents (.md, .txt, .rst, .pdf) from the given directory,
        embeds them, and makes them available to review_diff and review_file.

        Args:
            docs_path: Path to directory containing guideline files.
        """
        import os

        path = docs_path
        if not path:
            return "No path provided. Pass docs_path."

        if not os.path.isdir(path):
            return f"Directory not found: {path}"

        gl_store = get_guidelines_store(guidelines_path=path)
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
        file_stats = stats.get("file_graph", {})
        centrality = graph_runtime.centrality(top_n=5)
        cycles = graph_runtime.detect_cycles()

        parts = [
            f"Files: {file_stats.get('vertices', 0)}, "
            f"Edges: {file_stats.get('edges', 0)}, "
            f"DAG: {'Yes' if file_stats.get('is_dag') else 'No'}",
        ]

        betweenness = centrality.get("betweenness", [])
        if betweenness:
            top = [
                f"{item.get('file', '').rsplit('/', 1)[-1]} ({item.get('score', 0)})"
                for item in betweenness
                if item.get("score", 0) > 0
            ]
            if top:
                parts.append(f"Bottlenecks: {', '.join(top[:5])}")

        pagerank = centrality.get("pagerank", [])
        if pagerank:
            top_pr = [
                f"{item.get('file', '').rsplit('/', 1)[-1]}"
                for item in pagerank[:5]
            ]
            parts.append(f"Key files (PageRank): {', '.join(top_pr)}")

        if cycles.get("has_cycles"):
            cycle_groups = cycles.get("cycle_groups", [])
            parts.append(f"Cycles: {len(cycle_groups)} groups found")
            for i, group in enumerate(cycle_groups, 1):
                names = [f.rsplit('/', 1)[-1] for f in group]
                parts.append(f"  Cycle {i}: {' ↔ '.join(names)}")
            edges_to_break = cycles.get("edges_to_break", [])
            if edges_to_break:
                parts.append("Fix — remove these imports:")
                for edge in edges_to_break:
                    if len(edge) >= 2:
                        s, t = edge[0], edge[1]
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
        if not repo_path:
            return "Error: No repo path available."
        result = apply_fix_to_file(repo_path, file_path, old_code, new_code)
        if result["ok"]:
            notes = []
            if result.get("validation"):
                notes.append(result["validation"]["message"])
            return f"{result['message']}" + ("\n" + "\n".join(notes) if notes else "")
        return f"Error: {result['error']}"

    # ─── TOOL 13: verify_fix ─────────────────────────────────────
    @tool
    def verify_fix(file_paths: list[str] | None = None) -> str:
        """Run tests and static analysis to verify a fix.

        Call this AFTER apply_fix to check that the change didn't break anything.
        If file_paths is omitted, runs the full test suite.

        Args:
            file_paths: Optional list of changed files to focus verification on.
        """
        if not repo_path:
            return "Error: No repo path available."

        parts = ["## Verification Results"]

        # Static analysis
        sa_issues = run_static_analysis(repo_path, file_paths or [], language_hint=None)
        if sa_issues:
            parts.append(f"\nStatic analysis: {len(sa_issues)} issue(s)")
            for issue in sa_issues[:10]:
                loc = f"{issue.file_path}:{issue.line}" if issue.line else issue.file_path
                parts.append(f"- [{issue.severity}] {loc} — {issue.message} ({issue.tool})")
        else:
            parts.append("\nStatic analysis: no issues")

        # Tests
        test_result = run_tests(repo_path, file_paths or [])
        parts.append(f"\nTests: {'PASSED' if test_result.ok else 'FAILED'}")
        if test_result.command:
            parts.append(f"Command: {' '.join(test_result.command)}")
        if test_result.stdout:
            parts.append("```\n" + test_result.stdout[-1500:] + "\n```")
        if test_result.stderr:
            parts.append("stderr:\n```\n" + test_result.stderr[-1000:] + "\n```")
        if test_result.error:
            parts.append(f"Error: {test_result.error}")

        return "\n".join(parts)

    return [search_codebase, get_module_info, explain_function,
            get_overview, get_blast_radius_map, get_reading_order,
            get_execution_flow, review_diff, review_file, load_guidelines,
            get_architecture_health, apply_fix, verify_fix]

