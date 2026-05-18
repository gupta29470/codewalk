import logging
from collections import Counter, defaultdict

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

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
        # 1. It's a known wrapper name and has 50%+ of files (the main source dir)
        # 2. It's the ONLY subdirectory (single-child collapse)
        wrapper = top_dir.lower() in _WRAPPER_DIRS
        single = len(dir_counts) == 1

        if (top_count / file_with_dirs >= 0.5 and wrapper) or single:
            prefix_parts.append(top_dir)
            prefix = "/".join(prefix_parts) + "/"
            remaining = [
                file_path[len(prefix):] for file_path in remaining
                if file_path.startswith(prefix)
            ]
        else:
            break
    
    return "/".join(prefix_parts)

def _find_module_depth(file_paths: list[str], source_root: str) -> int:
    """Find the directory depth that represents the module boundary.

    Scans depths 1–5 looking for the level where child folder names
    start repeating across different parent directories (>50% shared).
    That repetition signals internal structure, so the level just above
    it is the module boundary.

    Returns:
        Depth (number of path components from source root) to use as
        the module name. Depth 1 = top-level folders, depth 2 = two
        levels deep (e.g. 'category/module').
    """
    # Strip source root and collect relative paths
    stripped = []
    for fp in file_paths:
        if source_root and fp.startswith(source_root + "/"):
            stripped.append(fp[len(source_root) + 1:])
        else:
            stripped.append(fp)

    # For each depth, find the level where:
    # - Names are mostly unique (reasonable fan-out)
    # - Names below repeat ACROSS siblings (same sub-folder names under different parents)
    # That repetition signals internal structure (bloc/, ui/, components/ in every feature)
    max_depth = 5
    best_depth = 1

    for depth in range(1, max_depth + 1):
        names_at_depth = []
        for path in stripped:
            parts = path.split("/")
            if len(parts) > depth:
                name = "/".join(parts[:depth])
                names_at_depth.append(name)

        if not names_at_depth:
            break

        unique = len(set(names_at_depth))

        # Key check: do DIFFERENT parents at this depth share the SAME child folder names?
        # e.g. features/foo/bloc, features/bar/bloc → "bloc" appears under multiple parents
        parent_to_children = defaultdict(set)
        for path in stripped:
            parts = path.split("/")
            if len(parts) > depth + 1:
                parent = "/".join(parts[:depth])
                child = parts[depth]
                parent_to_children[parent].add(child)

        if len(parent_to_children) >= 2:
            # Count how many child names appear under multiple parents
            all_children = []
            for children in parent_to_children.values():
                all_children.extend(children)
            child_counts = Counter(all_children)
            repeated_children = sum(1 for name, count in child_counts.items() if count >= 2)
            total_unique_children = len(child_counts)
            cross_parent_repeat = repeated_children / total_unique_children if total_unique_children else 0
        else:
            cross_parent_repeat = 0

        # This depth is the module level if:
        # - Reasonable fan-out (at least 3 unique modules)
        # - Child folders repeat across different parents (>50% of child names shared)
        # Once found, STOP — deeper levels are internal structure
        if unique >= 3 and cross_parent_repeat > 0.5:
            best_depth = depth
            break
        elif unique >= 3:
            best_depth = depth  # candidate, keep looking

    return best_depth


def _assign_modules(files: list[dict], source_root: str, module_depth: int) -> dict:
    """Assign files to modules based on source root and depth."""
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
            depth = module_depth
        else:
            relative_path = file_path
            # Files outside source root always use depth 1 (top-level dir)
            depth = 1

        # Module = path components up to detected depth, or "root" for root-level files
        parts = relative_path.split("/")
        if len(parts) > depth:
            module_name = "/".join(parts[:depth])
        elif len(parts) > 1:
            module_name = parts[0]
        else:
            module_name = "root"

        modules[module_name]["files"].append(file_path)
        modules[module_name]["languages"][file_info["language"]] += 1
        modules[module_name]["file_count"] += 1

    return dict(modules)


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

    # --- Step 2: find optimal module depth ---
    module_depth = _find_module_depth(file_paths, source_root)

    # --- Step 3: assign each file to a module ---
    modules = _assign_modules(files, source_root, module_depth)

    # Sanity check: if too many modules (>20), depth was too aggressive — fall back to depth 1
    if len(modules) > 20 and module_depth > 1:
        _log(f"[modules] Too many modules ({len(modules)}) at depth {module_depth}, falling back to depth 1")
        module_depth = 1
        modules = _assign_modules(files, source_root, module_depth)

    _log(f"[modules] Module depth: {module_depth} (root: {source_root or 'none'}, {len(modules)} modules)")

    # Convert Counter to dict for JSON serialization
    for mod in modules.values():
        mod["languages"] = dict(mod["languages"])
    
    # --- Step 4: build module-level dependency graph ---
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

    _log(f"[modules] Detected {len(modules)} modules from {len(files)} files (root: {source_root or 'none'})")
    return {
        "source_root": source_root,
        "modules": dict(modules),
        "module_graph": module_graph,
        "stats": {
            "total_modules": len(modules),
            "total_files": len(files),
        },
    }

