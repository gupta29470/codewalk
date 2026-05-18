"""
=============================================================================
 blast_radius.py - Impact Analysis (What Breaks If I Change This File?)
=============================================================================

WHAT THIS FILE DOES:
    Calculates the "blast radius" of changing any file. Given a file,
    it finds ALL other files that could be affected by a change, both
    directly (files that import it) and transitively (files that import
    THOSE files, and so on).

    Example:
        Change config.py -> directly affects: embedder.py, scanner.py, pipeline.py
                         -> transitively affects: server.py (imports pipeline.py)
                         -> Total blast radius: 4 files, risk level: "high"

HOW IT WORKS:
    1. REVERSE the dependency graph (forward: A imports B, reverse: B is imported BY A)
    2. BFS from the target file through the reversed graph
    3. Count depth levels: depth 1 = direct imports, depth 2+ = transitive
    4. Calculate risk level based on total affected / total files ratio

REAL-WORLD ANALOGY:
    Like a disease contact tracing map.
    If config.py gets "infected" (changed), who are the direct contacts
    (files importing it)? And who are THEIR contacts (transitive impact)?
    The more contacts = higher risk of widespread breakage.

WHERE IT'S CALLED:
    - server.py -> codewalk_get_blast_radius_map() MCP tool

DEPENDENCIES:
    - dependency_graph.py: provides the file-level graph to reverse

=============================================================================
"""

# --- Imports ---

from collections import deque


# =============================================================================
# build_reverse_graph() - Flip Edge Directions
# =============================================================================

def build_reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Reverse the dependency graph: edges go from 'imported' to 'importer'.

    FORWARD GRAPH (who does each file import?):
        pipeline.py -> [scanner.py, chunker.py]   (pipeline imports scanner)
        scanner.py  -> [config.py]                (scanner imports config)

    REVERSED GRAPH (who imports each file?):
        scanner.py -> [pipeline.py]    (scanner is imported BY pipeline)
        config.py  -> [scanner.py]     (config is imported BY scanner)

    WHY REVERSE?
        Blast radius = "if I change X, what breaks?"
        We need to find files that DEPEND ON X (importers of X).
        In the forward graph, X's row shows what X imports (irrelevant).
        In the reversed graph, X's row shows what imports X (exactly what we need).
    """
    internal_files = set(graph.keys())
    reverse = {file: [] for file in internal_files}

    for file, deps in graph.items():
        for dep in deps:
            if dep in internal_files:
                reverse[dep].append(file)

    return reverse


# =============================================================================
# get_blast_radius() - Single File Impact Analysis
# =============================================================================

def get_blast_radius(target_file: str, graph: dict[str, list[str]]) -> dict:
    """Calculate blast radius for a single file.

    ALGORITHM (BFS through reversed dependency graph):
        1. Reverse the graph
        2. Start at target_file
        3. BFS: visit all files that import target (depth 1 = direct)
        4. Then visit files that import THOSE files (depth 2 = transitive)
        5. Continue until no more reachable files

    DEPTH INTERPRETATION:
        depth 1: "Direct dependents" - they import the target file
        depth 2: "Transitive" - they import a direct dependent
        depth 3+: "Transitive" - further downstream

    RISK LEVELS (based on % of total codebase affected):
        critical: >50% OR 20+ files affected
        high:     >25% OR 10+ files affected
        moderate: >10% OR 4+ files affected
        low:      everything else

    Returns:
        {
            "file": "config.py",
            "direct": ["embedder.py", "scanner.py"],       <- depth 1
            "transitive": ["pipeline.py", "server.py"],    <- depth 2+
            "affected_files": 4,
            "risk_level": "high",
            "impact_tree": {"embedder.py": 1, "scanner.py": 1, "pipeline.py": 2, "server.py": 2}
        }
    """
    reverse = build_reverse_graph(graph)
    internal_files = set(graph.keys())

    if target_file not in internal_files:
        return {
            "file": target_file,
            "direct": [],
            "transitive": [],
            "affected_files": 0,
            "risk_level": "none",
            "impact_tree": {},
        }

    # BFS from target through reversed graph
    visited = {target_file}
    queue = deque()
    impact_tree = {}  # file -> depth (how many hops from target)

    # Seed with direct dependents (depth 1)
    for dependent in reverse.get(target_file, []):
        if dependent not in visited:
            queue.append((dependent, 1))
            visited.add(dependent)

    # BFS traversal
    while queue:
        current_file, depth = queue.popleft()
        impact_tree[current_file] = depth

        for dependent in reverse.get(current_file, []):
            if dependent not in visited:
                queue.append((dependent, depth + 1))
                visited.add(dependent)

    # Separate direct vs transitive
    direct = [file for file, depth in impact_tree.items() if depth == 1]
    transitive = [file for file, depth in impact_tree.items() if depth > 1]
    total_affected = len(impact_tree)
    total_files = len(internal_files)
    risk_level = _calculate_risk(total_affected, total_files)

    return {
        "file": target_file,
        "direct": sorted(direct),
        "transitive": sorted(transitive),
        "affected_files": total_affected,
        "risk_level": risk_level,
        "impact_tree": impact_tree,
    }


# =============================================================================
# _calculate_risk() - Risk Level Classification
# =============================================================================

def _calculate_risk(affected: int, total: int) -> str:
    """Classify risk based on number of affected files.

    THRESHOLDS (whichever triggers first):
        critical: >50% of codebase OR 20+ files
        high:     >25% of codebase OR 10+ files
        moderate: >10% of codebase OR 4+ files
        low:      everything else
    """
    if total == 0:
        return "none"
    ratio = affected / total
    if ratio > 0.5 or affected >= 20:
        return "critical"
    elif ratio > 0.25 or affected >= 10:
        return "high"
    elif ratio > 0.10 or affected >= 4:
        return "moderate"
    else:
        return "low"


# =============================================================================
# calculate_full_blast_map() - Every File's Blast Radius (Ranked)
# =============================================================================

def calculate_full_blast_map(graph: dict[str, list[str]]) -> dict:
    """Calculate blast radius for EVERY file, ranked by risk.

    OPTIMIZATION:
        Builds the reverse graph ONCE, then runs BFS for each file.
        Time complexity: O(V * (V + E)) where V=files, E=import edges.
        For a 100-file repo with 300 edges: ~30,000 operations (instant).

    USE CASE:
        "Which files are the riskiest to change?" - answered by sorting
        all files by their affected_files count.

    Returns:
        {
            "blast_map": [
                {"file": "config.py", "affected_files": 12, "risk_level": "critical", ...},
                {"file": "scanner.py", "affected_files": 5, "risk_level": "moderate", ...},
                ...
            ],
            "stats": {"total_files": 28, "critical_files": 2, "high_files": 3, ...},
            "highest_risk": "config.py"
        }
    """
    # Build reverse graph ONCE (shared by all BFS runs)
    reverse = build_reverse_graph(graph)
    internal_files = set(graph.keys())
    total_files = len(internal_files)

    results = []
    risk_counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0, "none": 0}

    for target_file in graph:
        # BFS from this file through reversed graph
        visited = {target_file}
        queue_bfs = deque()
        impact_tree = {}

        for dependent in reverse.get(target_file, []):
            if dependent not in visited:
                queue_bfs.append((dependent, 1))
                visited.add(dependent)

        while queue_bfs:
            current_file, depth = queue_bfs.popleft()
            impact_tree[current_file] = depth
            for dependent in reverse.get(current_file, []):
                if dependent not in visited:
                    queue_bfs.append((dependent, depth + 1))
                    visited.add(dependent)

        total_affected = len(impact_tree)
        risk_level = _calculate_risk(total_affected, total_files)

        results.append({
            "file": target_file,
            "affected_files": total_affected,
            "risk_level": risk_level,
            "direct_count": sum(1 for d in impact_tree.values() if d == 1),
            "transitive_count": sum(1 for d in impact_tree.values() if d > 1),
        })
        risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1

    # Sort by impact (most dangerous files first)
    results.sort(key=lambda x: x["affected_files"], reverse=True)
    highest_risk = results[0]["file"] if results else ""

    return {
        "blast_map": results,
        "stats": {
            "total_files": len(graph),
            "critical_files": risk_counts.get("critical", 0),
            "high_files": risk_counts.get("high", 0),
            "moderate_files": risk_counts.get("moderate", 0),
            "low_files": risk_counts.get("low", 0),
        },
        "highest_risk": highest_risk,
    }
