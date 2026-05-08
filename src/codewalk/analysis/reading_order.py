from collections import deque

def topological_sort(graph: dict[str, list[str]]) -> list[str]:
    """Sort files so dependencies come before dependents.

    Args:
        graph: file-level dependency graph from build_dependency_graph()
               {"path/a.py": ["path/b.py", "os"], "path/b.py": []}

    Returns:
        List of file paths in reading order (dependencies first).
        Files with no dependencies come first.
        External imports (not in graph) are ignored.
    """
    # Step 1: Filter to only internal files (keys of the graph)
    internal_files = set(graph.keys())

    # Step 2: Build in-degree count (how many internal deps each file has)
    in_degree = {file: 0 for file in internal_files}

    # Also build adjacency list (reverse: "who depends on me?")
    dependents = {file: [] for file in internal_files}

    for file, deps in graph.items():
        for dep in deps:
            if dep in internal_files:
                in_degree[file] += 1
                dependents[dep].append(file)

    # Step 3: Start with files that have zero in-degree (no internal deps)
    queue = deque(sorted(file for file in internal_files if in_degree[file] == 0))
    # sorted() for deterministic output — same input = same order every time

    result = []

    # Step 4: BFS — peel off zero-dependency files one at a time
    while queue:
        current = queue.popleft()
        result.append(current)

        # For everything that depends on current: reduce their in-degree
        for dependent in sorted(dependents[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Step 5: Any remaining files have circular deps — add them at the end
    remaining = [file for file in internal_files if file not in set(result)]
    result.extend(remaining)

    return result

def generate_reading_order(files: list[dict], deps: dict) -> dict:
    """Generate a complete reading order from scanned files and dependency graph.

    Args:
        files: from scan_directory() — list of file dicts
        deps: from build_dependency_graph() — {"graph": {...}, "stats": {...}}

    Returns:
        {
            "order": [                # sorted file list
                {"position": 1, "file": "config.py", "why": "No internal dependencies"},
                {"position": 2, "file": "file_filter.py", "why": "Used by: scanner.py"},
                ...
            ],
            "total_files": 15,
            "has_cycles": False       # True if circular deps detected
        }
    """
    graph = deps["graph"]
    sorted_files = topological_sort(graph)

    # Internal file set for cycle detection
    internal_files = set(graph.keys())
    has_cycles = len(sorted_files) < len(internal_files)

    # Build "used by" lookup — who imports this file?
    used_by = {file: [] for file in internal_files}
    for file, file_deps in graph.items():
        for dep in file_deps:
            if dep in internal_files:
                used_by[dep].append(file.split("/")[-1])

    # Build the order list with reasons
    order = []

    for index, file_path in enumerate(sorted_files):
        deps_list = [dep for dep in graph.get(file_path, []) if dep in internal_files]
        users = used_by.get(file_path, [])

        if not deps_list:
            why = "No internal dependencies"
        else:
            dep_names = [dep.split("/")[-1] for dep in deps_list]
            why = f"Depends on: {', '.join(dep_names)}"
        
        if users:
            why += f" | Used by: {', '.join(users)}"

        order.append({
            "position": index + 1,
            "file": file_path,
            "why": why,
        })
    
    return {
        "order": order,
        "total_files": len(sorted_files),
        "has_cycles": has_cycles,
    }