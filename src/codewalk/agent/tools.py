"""LangGraph agent tool definitions for codebase Q&A and modifications."""
from concurrent.futures import ThreadPoolExecutor

from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.graph.graph_runtime import GraphRuntime
from src.codewalk.query import (
    module_info_text, explain_function_text,
    overview_text, blast_radius_map_text, reading_order_text,
    execution_flow_text,
)
from src.codewalk.rag.chain import ask_corrective
from src.codewalk.rag.query_expander import expand_query
from src.codewalk.config import settings, get_llm
from src.codewalk.review.editor import apply_edit
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


_MAX_SEARCH_QUERIES = 3


def _synthesize_answers(question: str, answers: list[str]) -> str:
    """Merge answers from multiple retrieval queries into one coherent response."""
    if len(answers) == 1:
        return answers[0]

    llm = get_llm(temperature=0, reasoning=False)
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a senior engineer answering a question about a codebase. "
            "Below are partial answers produced from different search angles. "
            "Synthesize them into one concise, coherent answer. Preserve file paths "
            "and line numbers. Do not invent facts not present in the partial answers."
        )),
        ("human", (
            f"Question: {question}\n\n"
            + "\n\n---\n\n".join(f"Angle {i + 1}:\n{a}" for i, a in enumerate(answers))
        )),
    ])
    chain = prompt | llm
    response = chain.invoke({})
    return response.content.strip()


def _multi_query_search(query: str, store, graph_store=None) -> dict:
    """Expand the query into 1-3 searches and merge the results.

    Returns a dict matching ask_corrective's output shape:
        answer, confident, retrieval_confidence, relevant_chunks, retries
    """
    try:
        expanded = expand_query(query)
        queries = expanded.queries[:_MAX_SEARCH_QUERIES]
    except Exception:
        queries = [query]

    # Ensure the original query is always covered.
    if query not in queries:
        queries.insert(0, query)
    queries = queries[:_MAX_SEARCH_QUERIES]

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        futures = [executor.submit(ask_corrective, q, store, graph_store=graph_store) for q in queries]
        for future in futures:
            try:
                results.append(future.result())
            except Exception:
                continue

    if not results:
        return {
            "answer": "I couldn't retrieve any relevant code for that question.",
            "confident": False,
            "retrieval_confidence": 0.0,
            "relevant_chunks": 0,
            "retries": 0,
        }

    answers = [r.get("answer", "") for r in results if r.get("answer")]
    synthesized = _synthesize_answers(query, answers) if len(answers) > 1 else (answers[0] if answers else "")

    confidences = [r.get("retrieval_confidence", 0.0) or 0.0 for r in results]
    chunks = [r.get("relevant_chunks", 0) or 0 for r in results]
    retries = sum(r.get("retries", 0) or 0 for r in results)

    return {
        "answer": synthesized,
        "confident": any(r.get("confident") for r in results),
        "retrieval_confidence": max(confidences),
        "relevant_chunks": sum(chunks),
        "retries": retries,
    }


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

        Expands the query into 1-3 complementary search angles, runs
        corrective RAG for each in parallel, then synthesizes the results
        into a single answer. This improves recall compared to a single
        search, especially for broad or ambiguous questions.

        Use this for ANY question about code, functions, features, or
        implementation details.

        Args:
            query: Natural language question, e.g. "how does authentication work"
        """
        result = _multi_query_search(query, store, graph_store=graph_store)
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

    # ─── TOOL 8: load_guidelines ────────────────────────────────
    @tool
    def load_guidelines(docs_path: str = "") -> str:
        """Load project docs/standards for use in code reviews.

        Indexes .md/.txt/.rst/.pdf documents from the given directory. Reviews
        will automatically include an explicit `code_guidelines` file configured
        in codewalk.yaml, or any file named `code_guidelines.md`/`.txt`/`.rst`
        inside docs_path.

        Args:
            docs_path: Path to directory containing doc/guideline files.
        """
        import os
        from src.codewalk.doc_knowledge.doc_store import DocStore
        from src.codewalk.config import settings

        path = docs_path
        if not path:
            return "No path provided. Pass docs_path."

        if not os.path.isdir(path):
            return f"Directory not found: {path}"

        persist_dir = getattr(settings, "CHROMA_PERSIST_DIR", None) or ".codewalk/chroma"
        store = DocStore(persist_dir=persist_dir, collection_name="docs")
        store.create_collection()
        store.clear()
        count = store.index_docs(path)
        return f"Indexed {count} doc chunks from {path}"


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
        llm = get_llm(temperature=0, reasoning=False)
        result = apply_edit(repo_path, file_path, old_code=old_code, new_code=new_code, llm=llm)
        if result["ok"]:
            notes = []
            if result.get("validation"):
                notes.append(result["validation"]["message"])
            if result.get("attempts") and result["attempts"] > 1:
                notes.append(f"Applied after {result['attempts']} attempts")
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
            get_execution_flow, load_guidelines,
            get_architecture_health, apply_fix, verify_fix]

