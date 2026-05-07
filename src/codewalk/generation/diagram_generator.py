def generate_module_diagram(module_graph: dict, direction: str = "TD") -> str:
    """Generate a Mermaid flowchart from the module dependency graph.

    Args:
        module_graph: Dict mapping module_name → list of modules it depends on.
                      Example: {"rag": ["embeddings"], "embeddings": ["analysis"]}
        direction: Diagram direction — "TD" (top-down), "LR" (left-right),
                   "BT" (bottom-top), "RL" (right-left).

    Returns:
        Mermaid diagram string (without the ```mermaid fences).
    """
    lines = [f"graph {direction}"]

    has_edge = set()

    for module_name, dependencies in module_graph.items():
        for dependency in sorted(dependencies):
            lines.append(f"    {module_name} --> {dependency}")
            has_edge.add(module_name)
            has_edge.add(dependency)

    for module_name in sorted(module_graph.keys()):
        if module_name not in has_edge:
            lines.append(f"    {module_name}")

    return "\n".join(lines)

def generate_file_diagram(dep_graph: dict, max_files: int = 50) -> str:
    """Generate a Mermaid diagram from file-level dependencies.

    For large repos, only includes the most-connected files.

    Args:
        dep_graph: Dict mapping file_path → list of file_paths it imports.
                   From build_dependency_graph()["graph"].
        max_files: Maximum number of files to include (prevents huge diagrams).

    Returns:
        Mermaid diagram string.
    """
    connection_count = {}
    for source, targets in dep_graph.items():
        connection_count[source] = connection_count.get(source, 0) + len(targets)
        for target in targets:
            connection_count[target] = connection_count.get(target, 0) + 1

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
    """Make a file path safe for use as a Mermaid node ID.

    Mermaid doesn't allow / . - in node IDs.
    'src/auth/login.py' → 'src_auth_login_py'
    """
    return path.replace("/", "_").replace(".", "_").replace("-", "_")

def _short_name(path: str) -> str:
    """Get just the filename from a path for display in diagram box.

    'src/auth/login.py' → 'login.py'
    """
    return path.split("/")[-1]

