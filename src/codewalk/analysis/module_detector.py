"""
=============================================================================
 module_detector.py — Group Files Into Logical Modules
=============================================================================

WHAT THIS FILE DOES:
    Takes a flat list of files and groups them into "modules" (logical units)
    based on directory structure. Also builds a module-level dependency graph
    showing which modules depend on which other modules.

    Example output:
        "analysis" module: [code_parser.py, dependency_graph.py, module_detector.py]
        "embeddings" module: [embedder.py, vector_store.py, chunker.py]
        "mcp" module: [server.py]

HOW IT WORKS (4 STEPS):

    STEP 1 — Find Source Root
        Many repos have wrapper directories: src/codewalk/..., lib/app/...
        We strip those to find where the REAL module boundaries start.
        "src/codewalk/analysis/code_parser.py" → strip "src/codewalk" →
        now "analysis" is the module name.

    STEP 2 — Find Module Depth
        At what directory level are modules? Depth 1 = top-level folders.
        HEURISTIC: if child folder names REPEAT across different parents
        (e.g., every feature has /bloc/, /ui/, /models/), those are
        INTERNAL structure, not modules. The level ABOVE is the module boundary.

    STEP 3 — Assign Files to Modules
        Each file gets a module name based on its path at the detected depth.

    STEP 4 — Build Module Dependency Graph
        Using the file-level dependency graph (from dependency_graph.py),
        aggregate: if file A (in module X) imports file B (in module Y),
        then module X depends on module Y.

REAL-WORLD ANALOGY:
    Like an org chart. Individual employees (files) belong to departments
    (modules). The dependency graph shows which departments work together.
    "Engineering depends on Design" = some engineering files import design files.

WHERE IT'S CALLED:
    - pipeline.py → detect_modules() during the analysis phase
    - server.py → codewalk_get_module_info() tool

DEPENDENCIES:
    - dependency_graph.py: provides file-level dependency graph
    - log.py: logging

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging
from collections import Counter, defaultdict

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


# =============================================================================
# _WRAPPER_DIRS — Common Directory Names That Aren't Modules
# =============================================================================
# These are directories that just WRAP the real source code.
# They get stripped from paths before module detection.
# Example: "src/codewalk/analysis/..." → strip "src/codewalk" → "analysis/..." is the module.

_WRAPPER_DIRS = {
    "src", "lib", "app", "source", "packages", "pkg",
    "internal", "cmd", "main",
}


# =============================================================================
# Step 1: _find_source_root() — Strip Wrapper Directories
# =============================================================================

def _find_source_root(file_paths: list[str]) -> str:
    """Find and return the common wrapper prefix to strip.

    ALGORITHM:
        Repeatedly check: does 50%+ of files share the same top-level directory?
        If YES and it's a known wrapper name (src, lib, app...) → strip it.
        If YES and it's the ONLY subdirectory → strip it (single-child collapse).
        Repeat up to 5 levels deep.

    EXAMPLE:
        Files: ["src/codewalk/config.py", "src/codewalk/log.py", "README.md"]
        Iteration 1: "src" has 67% of files + is a wrapper → strip "src"
        Iteration 2: "codewalk" has 100% (single child) → strip "codewalk"
        Result: "src/codewalk" (this gets stripped from all paths)

    Returns:
        The prefix string to strip, e.g. "src/codewalk"
        Empty string if nothing to strip.
    """
    prefix_parts = []
    remaining = list(file_paths)

    for _ in range(5):  # Max 5 levels of stripping
        # Count which top-level directory each file belongs to
        dir_counts = Counter()
        file_with_dirs = 0

        for file_path in remaining:
            parts = file_path.split("/")
            if len(parts) > 1:  # Has at least one directory
                dir_counts[parts[0]] += 1
                file_with_dirs += 1

        if not dir_counts or file_with_dirs == 0:
            break

        # Find the most common top-level directory
        top_dir, top_count = dir_counts.most_common(1)[0]

        # Decision: should we strip this level?
        wrapper = top_dir.lower() in _WRAPPER_DIRS  # Known wrapper name?
        single = len(dir_counts) == 1               # Only one subdirectory?

        if (top_count / file_with_dirs >= 0.5 and wrapper) or single:
            prefix_parts.append(top_dir)
            prefix = "/".join(prefix_parts) + "/"
            # Remove prefix from remaining paths
            remaining = [
                file_path[len(prefix):] for file_path in remaining
                if file_path.startswith(prefix)
            ]
        else:
            break

    return "/".join(prefix_parts)


# =============================================================================
# Step 2: _find_module_depth() — Determine Module Boundary Level
# =============================================================================

def _find_module_depth(file_paths: list[str], source_root: str) -> int:
    """Find the directory depth that represents module boundaries.

    HEURISTIC:
        Scan depths 1-5. At each depth, check if child folder names
        REPEAT across different parent directories.

        If > 50% of child names appear under multiple parents → that means
        those are internal structure (bloc/, ui/, models/ in every feature).
        The level ABOVE is the module boundary.

    EXAMPLE (Flutter project):
        depth=1: features/, core/, shared/ → 3 unique, children don't repeat → candidate
        depth=2: features/auth/, features/home/ → children: bloc/, ui/, models/
                 "bloc" appears under BOTH auth and home → 60% repeat → STOP
        Result: depth=1 (features, core, shared are the modules)

    Returns:
        Integer depth (number of path components to use as module name).
    """
    # Strip source root
    stripped = []
    for fp in file_paths:
        if source_root and fp.startswith(source_root + "/"):
            stripped.append(fp[len(source_root) + 1:])
        else:
            stripped.append(fp)

    max_depth = 5
    best_depth = 1

    for depth in range(1, max_depth + 1):
        # Collect module names at this depth
        names_at_depth = []
        for path in stripped:
            parts = path.split("/")
            if len(parts) > depth:
                name = "/".join(parts[:depth])
                names_at_depth.append(name)

        if not names_at_depth:
            break

        unique = len(set(names_at_depth))

        # KEY CHECK: Do different parents share the same child folder names?
        parent_to_children = defaultdict(set)
        for path in stripped:
            parts = path.split("/")
            if len(parts) > depth + 1:
                parent = "/".join(parts[:depth])
                child = parts[depth]
                parent_to_children[parent].add(child)

        if len(parent_to_children) >= 2:
            # Count child names appearing under multiple parents
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
        # - At least 3 unique names (reasonable fan-out)
        # - AND children repeat across parents (>50% shared = internal structure)
        if unique >= 3 and cross_parent_repeat > 0.5:
            best_depth = depth
            break
        elif unique >= 3:
            best_depth = depth  # Candidate, keep looking

    return best_depth


# =============================================================================
# Step 3: _assign_modules() — Map Files to Module Names
# =============================================================================

def _assign_modules(files: list[dict], source_root: str, module_depth: int) -> dict:
    """Assign each file to a module based on its path.

    LOGIC:
        1. Strip source root from path
        2. Take first N path components (N = module_depth) as module name
        3. Files at root level (no subdirectory) → "root" module

    EXAMPLE (source_root="src/codewalk", depth=1):
        "src/codewalk/analysis/code_parser.py" → strip → "analysis/code_parser.py" → module="analysis"
        "src/codewalk/config.py" → strip → "config.py" → module="root" (no subdir)
    """
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
            depth = 1  # Files outside source root use depth 1

        # Module name = path components up to detected depth
        parts = relative_path.split("/")
        if len(parts) > depth:
            module_name = "/".join(parts[:depth])
        elif len(parts) > 1:
            module_name = parts[0]
        else:
            module_name = "root"  # Root-level files (config.py, etc.)

        modules[module_name]["files"].append(file_path)
        modules[module_name]["languages"][file_info["language"]] += 1
        modules[module_name]["file_count"] += 1

    return dict(modules)


# =============================================================================
# detect_modules() — The Main Entry Point
# =============================================================================

def detect_modules(files: list[dict], dep_graph: dict = None) -> dict:
    """Group files into logical modules and build module-level dependency graph.

    EXECUTION FLOW:
        1. Find source root (strip wrapper dirs)
        2. Find optimal module depth (where are the boundaries?)
        3. Assign files to modules
        4. Sanity check: if too many modules (>20), fall back to depth 1
        5. Build module-level dependency graph from file-level graph

    RETURNS:
        {
            "source_root": "src/codewalk",
            "modules": {
                "analysis": {
                    "files": ["src/codewalk/analysis/code_parser.py", ...],
                    "languages": {"python": 5},
                    "file_count": 5,
                },
                "embeddings": {
                    "files": ["src/codewalk/embeddings/embedder.py", ...],
                    "languages": {"python": 3},
                    "file_count": 3,
                },
                ...
            },
            "module_graph": {
                "analysis": ["ingestion"],      ← analysis imports from ingestion
                "embeddings": ["analysis"],     ← embeddings imports from analysis
                "mcp": ["analysis", "embeddings", "ingestion"],
            },
            "stats": {"total_modules": 5, "total_files": 20}
        }
    """
    file_paths = [file["file_path"] for file in files]

    # Step 1: Find source root
    source_root = _find_source_root(file_paths)

    # Step 2: Find optimal module depth
    module_depth = _find_module_depth(file_paths, source_root)

    # Step 3: Assign files to modules
    modules = _assign_modules(files, source_root, module_depth)

    # Sanity check: too many modules means depth was too aggressive
    if len(modules) > 20 and module_depth > 1:
        _log(f"[modules] Too many modules ({len(modules)}) at depth {module_depth}, falling back to depth 1")
        module_depth = 1
        modules = _assign_modules(files, source_root, module_depth)

    _log(f"[modules] Module depth: {module_depth} (root: {source_root or \'none\'}, {len(modules)} modules)")

    # Convert Counter → dict for JSON serialization
    for mod in modules.values():
        mod["languages"] = dict(mod["languages"])

    # Step 4: Build module-level dependency graph
    # Accept both full result dict {"graph": {...}} and raw graph dict
    if dep_graph and "graph" in dep_graph:
        dep_graph = dep_graph["graph"]

    module_graph = {}

    if dep_graph:
        # Map file_path → module_name for quick lookup
        file_to_module = {}
        for module_name, module_info in modules.items():
            for file_path in module_info["files"]:
                file_to_module[file_path] = module_name

        # For each module, find which OTHER modules its files import from
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

    _log(f"[modules] Detected {len(modules)} modules from {len(files)} files (root: {source_root or \'none\'})")
    return {
        "source_root": source_root,
        "modules": dict(modules),
        "module_graph": module_graph,
        "stats": {
            "total_modules": len(modules),
            "total_files": len(files),
        },
    }
