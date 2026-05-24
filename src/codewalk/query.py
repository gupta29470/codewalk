"""Core query logic shared by MCP server, LangGraph agent, and FastAPI API.

Each function takes explicit data arguments (no global state dependency)
and returns a formatted markdown string ready for display.
"""

from __future__ import annotations

from collections import Counter

from src.codewalk.analysis.blast_radius import get_blast_radius, calculate_full_blast_map
from src.codewalk.analysis.reading_order import generate_reading_order_raw
from src.codewalk.graph.graph_runtime import GraphRuntime
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.rag.chain import format_context
from src.codewalk.rag.retrieval_quality import filter_by_distance


# ─── Shared helpers ──────────────────────────────────────────────────

def resolve_module_name(module_name: str, modules: dict) -> str | None:
    """Case-insensitive module name lookup. Returns actual name or None."""
    for name in modules:
        if name.lower() == module_name.lower():
            return name
    return None


def module_not_found_error(module_name: str, modules: dict) -> str:
    """Standard error message when a module isn't found."""
    available = ", ".join(sorted(modules.keys()))
    return f"Module '{module_name}' not found. Available: {available}"


def short_name(path: str) -> str:
    """Get the filename from a full path: 'src/foo/bar.py' → 'bar.py'."""
    return path.split("/")[-1]


def compute_file_risks(file_paths: list[str], runtime) -> tuple[list[dict], str]:
    """Per-file blast radius → sorted list of structured dicts + max risk level.

    Each dict: {file, risk_level, affected_files, direct: [short], transitive: [short]}.
    Used by both API (returns JSON) and MCP/agent (formats to markdown).
    """
    risk_order = {"critical": 4, "high": 3, "moderate": 2, "low": 1, "none": 0}
    max_risk = "low"
    results = []
    for file_path in file_paths:
        radius = get_blast_radius(file_path, runtime)
        if risk_order.get(radius["risk_level"], 0) > risk_order.get(max_risk, 0):
            max_risk = radius["risk_level"]
        results.append({
            "file": file_path,
            "risk_level": radius["risk_level"],
            "affected_files": radius["affected_files"],
            "direct": [short_name(f) for f in radius["direct"]],
            "transitive": [short_name(f) for f in radius["transitive"]],
        })
    results.sort(key=lambda x: x["affected_files"], reverse=True)
    return results, max_risk


def resolve_module_with_fallback(
    module_name: str, modules_result: dict, files: list[dict] | None = None
) -> tuple[str | None, dict | None, bool]:
    """Module lookup with sub-folder fallback.

    Returns (actual_name, info_dict, matched_as_feature).
    Returns (None, None, False) if not found.
    """
    modules = modules_result.get("modules", {})
    actual_name = resolve_module_name(module_name, modules)

    if actual_name is not None:
        return actual_name, modules[actual_name], False

    source_root = modules_result.get("source_root", "")
    for mod_name, mod_info in modules.items():
        prefix = (
            f"{source_root}/{mod_name}/{module_name.lower()}/"
            if source_root
            else f"{mod_name}/{module_name.lower()}/"
        )
        matching_files = [f for f in mod_info["files"] if f.lower().startswith(prefix.lower())]
        if matching_files:
            lang_counter = Counter()
            if files:
                matching_set = set(matching_files)
                for f in files:
                    if f["file_path"] in matching_set:
                        lang_counter[f["language"]] += 1
            info = {
                "files": matching_files,
                "file_count": len(matching_files),
                "languages": dict(lang_counter),
            }
            return mod_name, info, True

    return None, None, False


# ─── Query functions ─────────────────────────────────────────────────

def search_codebase_text(store, query: str) -> str:
    """Semantic search against ChromaDB embeddings."""
    results = store.search(query, n_results=5)
    filtered, _ = filter_by_distance(results)
    if not filtered:
        return "No relevant code found for that query."
    return format_context(filtered)


def module_info_text(modules_result: dict, module_name: str,
                     graph_runtime: GraphRuntime | None = None,
                     graph_store: GraphStore | None = None) -> str:
    """Basic module info: files, languages, dependencies."""
    modules = modules_result.get("modules", {})
    module_graph = modules_result.get("module_graph", {})

    actual_name = resolve_module_name(module_name, modules)
    if actual_name is None:
        return module_not_found_error(module_name, modules)

    info = modules[actual_name]
    depends_on = module_graph.get(actual_name, [])
    depended_by = [other for other, d in module_graph.items() if actual_name in d]
    hub_section = ""
    if graph_runtime and hasattr(graph_runtime, 'centrality'):
        centrality = graph_runtime.centrality(top_n=5)
        module_files = set(info["files"])
        # Filter centrality to only files in this module
        hub_files = [
            item for item in centrality.get("betweenness", [])
            if item["file"] in module_files and item["score"] > 0
        ]
        if hub_files:
            hub_names = [f"{h['file'].rsplit('/', 1)[-1]} ({h['score']})" for h in hub_files[:3]]
            hub_section = f"\n**Hub files:** {', '.join(hub_names)}"

    coupling_section = ""
    if graph_store:
        outgoing = 0
        incoming = 0
        for file in info["files"]:
            imports = graph_store.get_imports(file)
            importers = graph_store.get_importers(file)
            outgoing += sum(1 for imp in imports if imp not in info["files"])
            incoming += sum(1 for imp in importers if imp not in info["files"])
        if outgoing or incoming:
            coupling_section = f"\n**Coupling:** {outgoing} outgoing, {incoming} incoming cross-module edges"

    file_names = [short_name(path) for path in sorted(info["files"])]
    lang_str = ", ".join(
        f"{lang} ({count} files)" for lang, count in sorted(info["languages"].items())
    )

    return "\n".join([
        f"## Module: {actual_name}",
        f"**Files ({info['file_count']}):** {', '.join(file_names)}",
        f"**Languages:** {lang_str}",
        f"**Depends on:** {', '.join(depends_on) or 'None (standalone)'}",
        f"**Depended on by:** {', '.join(depended_by) or 'None'}",
    ]) + hub_section + coupling_section


def explain_function_text(store, function_name: str,
                          deps: dict = None,
                          graph_runtime: GraphRuntime | None = None,
                          graph_store: GraphStore | None = None) -> str:
    """Look up a function/class in ChromaDB and explain with blast radius."""
    results = store.search(function_name, n_results=10)
    filtered, _ = filter_by_distance(results)
    matches = [
        r for r in filtered
        if function_name.lower() in r["metadata"].get("symbol_name", "").lower()
    ]
    to_show = matches[:3] if matches else filtered[:3] if filtered else []
    if not to_show:
        return f"Function '{function_name}' not found in the codebase."

    context = format_context(to_show)

    file_path = to_show[0]["metadata"].get("file_path", "")
    if file_path and deps:
        runtime = graph_runtime or deps["graph"]
        radius = get_blast_radius(file_path, runtime)
        risk = radius["risk_level"].upper()
        affected = radius["affected_files"]
        direct_names = [short_name(f) for f in radius["direct"]]
        transitive_names = [short_name(f) for f in radius["transitive"]]
        breaks = f"Direct: {', '.join(direct_names)}" if direct_names else "No direct dependents"
        if transitive_names:
            breaks += f" | Transitive: {', '.join(transitive_names)}"
        context += (
            f"\n\n### Blast Radius\n"
            f"**Risk:** {risk} — {affected} files affected\n"
            f"**{breaks}**"
        )

    if graph_store and file_path:
        symbol_name = to_show[0]["metadata"].get("symbol_name", function_name)
        qualified_name = f"{file_path}:{symbol_name}"

        callers = graph_store.get_callers_of_symbol(qualified_name)
        callees = graph_store.get_callees_of_symbol(qualified_name)

        if callers:
            caller_lines = [
                f"  - {c['caller']}() at {c['file']}:{c['line']}"
                for c in callers[:10]
            ]
            context += (
                f"\n\n### Called by ({len(callers)} caller{'s' if len(callers) != 1 else ''}):\n"
                + "\n".join(caller_lines)
            )
        if callees:
            callee_lines = [
                f"  - {c['callee']}() at {c['file']}:{c['line']}"
                for c in callees[:10]
            ]
            context += (
                f"\n\n### Calls ({len(callees)} function{'s' if len(callees) != 1 else ''}):\n"
                + "\n".join(callee_lines)
            )

    return context


def overview_text(repo_path: str, modules_result: dict, deps: dict,
                  graph_runtime: GraphRuntime | None = None) -> str:
    """Project overview: tech stack, modules, dependency flow, riskiest files."""
    tech = detect_tech_stack(repo_path)

    runtime = graph_runtime or deps["graph"]
    blast_map = calculate_full_blast_map(runtime)
    top_risky = blast_map["blast_map"][:30]

    risky_lines = []
    for item in top_risky:
        file_path = item["file"]
        name = short_name(file_path)
        risk = item["risk_level"].upper()
        affected = item["affected_files"]
        radius = get_blast_radius(file_path, runtime)
        direct = [short_name(f) for f in radius["direct"]]
        risky_lines.append(
            f"  [{risk}] {name} — {affected} affected | breaks: {', '.join(direct)}"
        )
    risky_section = "\n".join(risky_lines) if risky_lines else "  No high-risk files"

    modules_info = modules_result["modules"]
    module_lines = []
    for name, info in sorted(modules_info.items()):
        lang_str = ", ".join(f"{l}({c})" for l, c in sorted(info["languages"].items()))
        module_lines.append(f"  - {name} ({info['file_count']} files): {lang_str}")
    modules_section = "\n".join(module_lines)

    module_graph = modules_result.get("module_graph", {})

    depended_on = set()
    for dep_list in module_graph.values():
        depended_on.update(dep_list)
    entry_modules = sorted(m for m in module_graph if m not in depended_on)

    dep_count = Counter()
    for dep_list in module_graph.values():
        dep_count.update(dep_list)
    core_modules = [name for name, _ in dep_count.most_common(3)] if dep_count else []

    flow_lines = []
    for mod_name in sorted(module_graph.keys()):
        mod_deps = module_graph.get(mod_name, [])
        if mod_deps:
            flow_lines.append(f"  {mod_name} → {', '.join(mod_deps)}")
        else:
            flow_lines.append(f"  {mod_name} → (standalone, no dependencies)")
    flow_section = "\n".join(flow_lines)

    centrality_section = ""
    if hasattr(runtime, "centrality"):
        centrality = runtime.centrality(top_n=5)
        if centrality.get("pagerank"):
            pagerank_names = [item["file"].rsplit("/", 1)[-1] for item in centrality["pagerank"][:5]]
            centrality_section = (
                f"\n\n### Key Files (PageRank)\n"
                f"Most important files by transitive dependency weight:\n"
                f"  {', '.join(pagerank_names)}"
            )

    cycle_section = ""
    if hasattr(runtime, 'detect_cycles'):
        cycles = runtime.detect_cycles()
        if cycles["has_cycles"]:
            count = len(cycles["cycle_groups"])
            cycle_section = (
                f"\n\n### ⚠ Circular Dependencies\n"
                f"{count} cycle group(s) detected. Run `codewalk_get_architecture_health` for details."
            )

    return (
        f"## Project Overview\n\n"
        f"**Tech Stack:** {', '.join(tech) if tech else 'Not detected'}\n"
        f"**Total Files:** {modules_result['stats']['total_files']}\n"
        f"**Total Modules:** {modules_result['stats']['total_modules']}\n\n"
        f"### Modules\n{modules_section}\n\n"
        f"### Module Dependency Flow\n"
        f"**Entry points** (top-level, nothing depends on these): {', '.join(entry_modules) or 'None'}\n"
        f"**Core modules** (most depended on): {', '.join(core_modules) or 'None'}\n\n"
        f"{flow_section}\n\n"
        f"### Riskiest Files ...\n{risky_section}{centrality_section}{cycle_section}"
    )


def blast_radius_map_text(modules_result: dict, deps: dict,
                          target: str = "",
                          graph_runtime: GraphRuntime | None = None) -> str:
    """Blast radius report for a target module, file, or top 30 riskiest."""
    graph = deps["graph"]
    runtime = graph_runtime or graph

    if target:
        modules = modules_result.get("modules", {})
        actual_module = resolve_module_name(target, modules)
        if actual_module:
            target_files = sorted(modules[actual_module]["files"])
            scope = f"module '{actual_module}'"
        else:
            matched = [f for f in graph.keys() if short_name(f) == target or f.endswith(target)]
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

    all_risks, max_risk = compute_file_risks(target_files, runtime)

    if not target:
        all_risks = [r for r in all_risks if r["affected_files"] > 0][:30]

    lines = []
    for entry in all_risks:
        risk = entry["risk_level"].upper()
        affected = entry["affected_files"]
        short_path = "/".join(entry["file"].split("/")[-2:])
        if affected > 0:
            breaks = f"breaks: {', '.join(entry['direct'])}"
            if entry["transitive"]:
                breaks += f" → then: {', '.join(entry['transitive'])}"
            lines.append(f"  [{risk}] {short_path} — {affected} affected | {breaks}")
        else:
            lines.append(f"  [SAFE] {short_path} — no dependents")

    header = (
        f"## Blast Radius — {scope}\n"
        f"**Overall risk:** {max_risk.upper()}\n"
        f"**Files shown:** {len(lines)}\n"
    )
    return header + "\n" + "\n".join(lines)


def reading_order_text(files: list[dict], deps: dict, modules_result: dict,
                       module_name: str = "",
                       graph_runtime: GraphRuntime | None = None) -> str:
    """Reading order: all files in dependency order with blast radius risk."""
    order = generate_reading_order_raw(files, deps, graph_runtime=graph_runtime)
    runtime = graph_runtime or deps["graph"]

    all_items = order["order"]

    scope = "entire repo"
    if module_name:
        modules = modules_result.get("modules", {})
        actual_name = resolve_module_name(module_name, modules)
        if actual_name is None:
            return module_not_found_error(module_name, modules)
        module_files = set(modules[actual_name]["files"])
        all_items = [item for item in all_items if item["file"] in module_files]
        scope = f"module '{actual_name}'"

    lines = []
    for item in all_items:
        radius = get_blast_radius(item["file"], runtime)
        risk = radius["risk_level"].upper()
        pos = item["position"]
        why = item["why"]
        affected = radius["affected_files"]
        lines.append(f"{pos}. [{risk}] {item['file']} ({affected} affected) — {why}")

    header = f"## Reading Order — {scope} ({len(all_items)} files)"
    return header + "\n" + "\n".join(lines)


def execution_flow_text(modules_result: dict, deps: dict,
                        module_name: str = "") -> str:
    """Execution flow: module-to-module or file-to-file dependencies."""
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
        actual_name = resolve_module_name(module_name, modules)
        if actual_name is None:
            return module_not_found_error(module_name, modules)

        graph = deps["graph"]
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
                parts.append(f"imports: {', '.join(short_name(d) for d in in_module)}")
            if cross_module:
                parts.append(f"external: {', '.join(short_name(d) for d in cross_module)}")
            if parts:
                dep_lines.append(f"  {short_name(file_path)} → {' | '.join(parts)}")
            else:
                dep_lines.append(f"  {short_name(file_path)} → (no internal imports)")

        entry_names = [short_name(f) for f in entry_files]

        return (
            f"## Execution Flow — {actual_name} (file level)\n"
            f"**Entry files** (nothing in this module imports these): {', '.join(entry_names)}\n"
            f"**Files:** {len(target_files)}\n\n"
            f"### File Dependencies\n"
            + "\n".join(dep_lines)
        )
