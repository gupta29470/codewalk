from langchain_core.tools import tool

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.chain import format_context
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.generation.diagram_generator import generate_module_diagram
from src.codewalk.analysis.blast_radius import get_blast_radius, calculate_full_blast_map
from src.codewalk.analysis.reading_order import generate_reading_order_raw
from src.codewalk.review.reviewer import review_diff as _review_diff
from src.codewalk.config import settings

def create_tools(store: VectorStore, modules_result: dict,
                 files: list[dict] = None, deps: dict = None) -> list:
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
    
    # ─── TOOL 4: get_overview ────────────────────────────────────
    @tool
    def get_overview() -> str:
        """Get a high-level overview of the analyzed codebase.

        Returns tech stack, module list, dependency diagram, and
        riskiest files. Use when user asks "what is this project" or
        "give me an overview".
        """
        if deps is None:
            return "Error: No analysis data available."
        
        repo_path = settings.repo_path
        tech = detect_tech_stack(repo_path)
        diagram = generate_module_diagram(modules_result["module_graph"])
        modules = list(modules_result["modules"].keys())

        blast_map = calculate_full_blast_map(deps["graph"])
        top_risky = blast_map["blast_map"][:30]

        risky_lines = []
        for item in top_risky:
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
            f"**Files:** {modules_result['stats']['total_files']}\n"
            f"**Modules ({len(modules)}):** {', '.join(modules)}\n\n"
            f"### Dependency Diagram\n```mermaid\n{diagram}\n```\n\n"
            f"### Riskiest Files (blast radius)\n{risky_section}"
        )
    
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

        graph = deps["graph"]

        if target:
            modules = modules_result.get("modules", {})
            actual_module = None
            for name in modules:
                if name.lower() == target.lower():
                    actual_module = name
                    break

            if actual_module:
                target_files = sorted(modules[actual_module]["files"])
                scope = f"module '{actual_module}'"
            else:
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
            scope = "top 30 riskiest"

        risk_order = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "none": 0}
        max_risk = "low"
        results = []

        for file_path in target_files:
            radius = get_blast_radius(file_path, graph)
            if risk_order.get(radius["risk_level"], 0) > risk_order.get(max_risk, 0):
                max_risk = radius["risk_level"]
            results.append((file_path, radius))

        results.sort(key=lambda x: x[1]["affected_files"], reverse=True)

        if not target:
            results = [r for r in results if r[1]["affected_files"] > 0][:30]

        lines = []
        for file_path, radius in results:
            risk = radius["risk_level"].upper()
            affected = radius["affected_files"]
            if affected > 0:
                direct = [f.split("/")[-1] for f in radius["direct"]]
                transitive = [f.split("/")[-1] for f in radius["transitive"]]
                breaks = f"breaks: {', '.join(direct)}"
                if transitive:
                    breaks += f" → then: {', '.join(transitive)}"
                lines.append(f"  [{risk}] {file_path} — {affected} affected | {breaks}")
            else:
                lines.append(f"  [SAFE] {file_path} — no dependents")

        header = (
            f"## Blast Radius — {scope}\n"
            f"**Overall risk:** {max_risk.upper()}\n"
            f"**Files shown:** {len(lines)}\n"
        )

        return header + "\n" + "\n".join(lines)
    
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
        
        order = generate_reading_order_raw(files, deps)
        graph = deps["graph"]

        all_items = order["order"]

        scope = "entire repo"
        if module_name:
            modules = modules_result.get("modules", {})
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

        module_graph = modules_result.get("module_graph", {})
        modules = modules_result.get("modules", {})

        if not module_name:
            depended_on = set()
            for dep_list in module_graph.values():
                depended_on.update(dep_list)
            entry_modules = sorted(m for m in module_graph if m not in depended_on)

            lines = []
            for mod_name in sorted(module_graph.keys()):
                mod_deps = module_graph.get(mod_name, [])
                file_count = modules[mod_name]["file_count"] if mod_name in modules else "?"
                if mod_deps:
                    lines.append(f"  {mod_name} ({file_count} files) → depends on: {', '.join(mod_deps)}")
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
            actual_name = None
            for name in modules:
                if name.lower() == module_name.lower():
                    actual_name = name
                    break
            if actual_name is None:
                available = ", ".join(sorted(modules.keys()))
                return f"Module '{module_name}' not found. Available: {available}"

            graph = deps["graph"]
            internal_files = set(graph.keys())
            module_file_set = set(modules[actual_name]["files"])
            target_files = sorted(file for file in graph.keys() if file in module_file_set)

            imported_in_module = set()
            for file in target_files:
                for dep in graph.get(file, []):
                    if dep in module_file_set:
                        imported_in_module.add(dep)
            entry_files = [file for file in target_files if file not in imported_in_module]

            dep_lines = []
            for file_path in target_files:
                internal_deps = [dep for dep in graph.get(file_path, []) if dep in internal_files]
                in_module = [dep for dep in internal_deps if dep in module_file_set]
                cross_module = [dep for dep in internal_deps if dep not in module_file_set]
                parts = []
                if in_module:
                    parts.append(f"imports: {', '.join(dep.split('/')[-1] for dep in in_module)}")
                if cross_module:
                    parts.append(f"external: {', '.join(dep.split('/')[-1] for dep in cross_module)}")
                if parts:
                    dep_lines.append(f"  {file_path.split('/')[-1]} → {' | '.join(parts)}")
                else:
                    dep_lines.append(f"  {file_path.split('/')[-1]} → (no internal imports)")

            entry_names = [file.split("/")[-1] for file in entry_files]

            return (
                f"## Execution Flow — {actual_name} (file level)\n"
                f"**Entry files** (nothing in this module imports these): {', '.join(entry_names)}\n"
                f"**Files:** {len(target_files)}\n\n"
                f"### File Dependencies\n"
                + "\n".join(dep_lines)
            )

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



