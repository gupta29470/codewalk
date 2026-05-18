"""
=============================================================================
 diagram_generator.py - Mermaid Diagram Generation
=============================================================================

WHAT THIS FILE DOES:
    Generates Mermaid-format diagrams from dependency graphs.
    Two levels:
      1. Module-level: shows how modules connect (overview)
      2. File-level: shows how individual files import each other (detailed)

    Output is a Mermaid string that can be rendered by any Mermaid viewer
    (GitHub, VS Code, documentation sites).

WHERE IT'S CALLED:
    - server.py -> codewalk_get_execution_flow() MCP tool
    - overview_generator.py -> included in the overview document

DEPENDENCIES:
    - None (pure string generation, no external deps)

=============================================================================
"""


def generate_module_diagram(module_graph: dict, direction: str = "TD") -> str:
    """Generate Mermaid flowchart from module dependency graph.

    Args:
        module_graph: {"rag": ["embeddings"], "embeddings": ["analysis"]}
        direction: "TD" (top-down), "LR" (left-right), "BT", "RL"

    Returns:
        Mermaid diagram string (without ``` fences).
        Example: "graph TD\n    rag --> embeddings\n    embeddings --> analysis"
    """
    lines = [f"graph {direction}"]
    has_edge = set()

    for module_name, dependencies in module_graph.items():
        for dependency in sorted(dependencies):
            lines.append(f"    {module_name} --> {dependency}")
            has_edge.add(module_name)
            has_edge.add(dependency)

    # Add isolated modules (no dependencies and nothing depends on them)
    for module_name in sorted(module_graph.keys()):
        if module_name not in has_edge:
            lines.append(f"    {module_name}")

    return "\n".join(lines)


def generate_file_diagram(dep_graph: dict, max_files: int = 50) -> str:
    """Generate Mermaid diagram from file-level dependencies.

    For large repos, only includes the most-connected files to prevent
    unreadable diagrams.

    Args:
        dep_graph: {"auth.py": ["db.py", "config.py"], ...}
        max_files: Cap on files shown (default 50)

    Returns:
        Mermaid diagram string with file nodes and import edges.
    """
    # Rank files by connection count (imports + imported-by)
    connection_count = {}
    for source, targets in dep_graph.items():
        connection_count[source] = connection_count.get(source, 0) + len(targets)
        for target in targets:
            connection_count[target] = connection_count.get(target, 0) + 1

    # Take top N most-connected files
    top_files = sorted(
        connection_count.keys(),
        key=lambda f: connection_count[f],
        reverse=True
    )[:max_files]
    top_set = set(top_files)

    lines = ["graph LR"]
    for source in sorted(top_files):
        if source not in dep_graph:
            continue
        for target in sorted(dep_graph[source]):
            if target in top_set:
                source_id = _sanitize_id(source)
                target_id = _sanitize_id(target)
                source_label = _short_name(source)
                target_label = _short_name(target)
                lines.append(f'    {source_id}["{source_label}"] --> {target_id}["{target_label}"]')

    return "\n".join(lines)


def _sanitize_id(path: str) -> str:
    """Make file path safe for Mermaid node ID. Replaces /.-  with _"""
    return path.replace("/", "_").replace(".", "_").replace("-", "_")


def _short_name(path: str) -> str:
    """Get just filename from path for diagram labels."""
    return path.split("/")[-1]