from collections import Counter, defaultdict

_WRAPPER_DIRS = {
    "src", "lib", "app", "source", "packages", "pkg",
    "internal", "cmd", "main",
}

def _find_source_root(file_paths: list[str]) -> str:
    """Find wrapper directories to strip before detecting modules.

    Walks down the directory tree, stripping one level at a time
    if 90%+ of files share the same prefix AND it's a wrapper dir
    (or 100% share it — single-child collapse).

    Returns:
        The prefix to strip, e.g. "src/codewalk".
        Empty string if nothing to strip.
    """
    prefix_parts = []
    remaining = list(file_paths)

    for _ in range(5): # max 5 levels
        # Count first-level directories in remaining paths
        dir_counts = Counter()
        file_with_dirs = 0

        for file_path in remaining:
            parts = file_path.split("/")
            if len(parts) > 1:
                dir_counts[parts[0]] += 1
                file_with_dirs += 1

        if not dir_counts or file_with_dirs == 0:
            break

        top_dir, top_count = dir_counts.most_common(1)[0]

        # Two reasons to strip:
        # 1. It's a known wrapper name and has 90%+ of files
        # 2. It's the ONLY subdirectory (single-child collapse)
        wrapper = top_dir.lower() in _WRAPPER_DIRS
        single = len(dir_counts) == 1

        if (top_count / file_with_dirs >= 0.9 and wrapper) or single:
            prefix_parts.append(top_dir)
            prefix = "/".join(prefix_parts) + "/"
            remaining = [
                file_path[len(prefix):] for file_path in remaining
                if file_path.startswith(prefix)
            ]
        else:
            break
    
    return "/".join(prefix_parts)

def detect_modules(files: list[dict], dep_graph: dict = None) -> dict:
    """Group files into logical modules and build module-level dependency graph.

    Args:
        files: List of file dicts from scanner.scan_directory().
        dep_graph: Optional result from build_dependency_graph().
                   Accepts both the full result dict or just the "graph" dict.

    Returns:
        {
            "source_root": "src/codewalk",
            "modules": {
                "analysis": {
                    "files": ["src/codewalk/analysis/code_parser.py", ...],
                    "languages": {"python": 3},
                    "file_count": 3,
                },
                ...
            },
            "module_graph": {
                "analysis": ["ingestion"],   # analysis depends on ingestion
                "rag": ["embeddings"],
                ...
            },
            "stats": {
                "total_modules": 5,
                "total_files": 20,
            }
        }
    """
    file_paths = [file["file_path"] for file in files]

    # --- Step 1: find source root ---
    source_root = _find_source_root(file_paths)

    # --- Step 2: assign each file to a module ---
    modules = defaultdict(lambda: {
        "files": [],
        "languages": Counter(),
        "file_count": 0,
    })

    for file_info in files:
        file_path = file_info["file_path"]

        # Strip source root prefix
        if source_root and file_path.startswith(source_root + "/"):
            relative_path = file_path[len(source_root) + 1:]
        else:
            relative_path = file_path

        # Module = first directory component, or "root" for root-level files
        parts = relative_path.split("/")
        module_name = parts[0] if len(parts) > 1 else "root"

        modules[module_name]["files"].append(file_path)
        modules[module_name]["languages"][file_info["language"]] += 1
        modules[module_name]["file_count"] += 1

    # Convert Counter to dict for JSON serialization
    for mod in modules.values():
        mod["languages"] = dict(mod["languages"])
    
    # --- Step 3: build module-level dependency graph ---
    # Accept both full result dict and raw graph dict
    if dep_graph and "graph" in dep_graph:
        dep_graph = dep_graph["graph"]
    
    module_graph = {}

    if dep_graph:
        # Map file_path → module_name for quick lookup
        file_to_module = {}
        for module_name, module_info in modules.items():
            for file_path in module_info["files"]:
                file_to_module[file_path] = module_name
            
        # For each module, find which OTHER modules it depends on
        for module_name in modules:
            deps = set()
            for file_path in modules[module_name]["files"]:
                if file_path in dep_graph:
                    for target in dep_graph[file_path]:
                        target_module = file_to_module.get(target)
                        if target_module and target_module != module_name:
                            deps.add(target_module)
            module_graph[module_name] = sorted(deps)
    else:
        for module_name in modules:
            module_graph[module_name] = []

    return {
        "source_root": source_root,
        "modules": dict(modules),
        "module_graph": module_graph,
        "stats": {
            "total_modules": len(modules),
            "total_files": len(files),
        },
    }

