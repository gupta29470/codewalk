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

from src.codewalk.graph.graph_runtime import GraphRuntime

def build_reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Reverse the dependency graph: edges go from 'imported' → 'importer'."""

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

    source: GraphRuntime (igraph, C-speed) or dict (legacy Python BFS).

    EXAMPLE (igraph path — codewalk's own src, target = "config.py"):
        target_file = "config.py"
        graph = GraphRuntime instance

        idx = graph._find_vertex(file_graph, "config.py") = 5

        distances = file_graph.shortest_paths(source=5, mode="in")[0]
        # distances is a list of 56 floats, one per vertex:
        # distances = [3.0, 2.0, inf, inf, 1.0, 0.0, 1.0, inf, ...]
        #              ^^^   ^^^               ^^^  ^^^  ^^^
        #              |     |                 |    |    embedder.py (depth 1)
        #              |     |                 |    config.py itself (depth 0)
        #              |     |                 scanner.py (depth 1)
        #              |     pipeline.py (depth 2)
        #              state.py (depth 3)

        impact_tree = {
            "scanner.py": 1,     # depth 1 = direct importer
            "embedder.py": 1,    # depth 1 = direct importer
            "pipeline.py": 2,    # depth 2 = transitive (imports scanner)
            "state.py": 3,       # depth 3 = transitive (imports pipeline)
        }

        direct = ["embedder.py", "scanner.py"]          # distance == 1
        transitive = ["pipeline.py", "state.py"]         # distance > 1
        total_affected = 4
        risk_level = _calculate_risk(4, 56) = "low"     # 4/56 = 7% < 10%

        returns {
            "file": "config.py",
            "direct": ["embedder.py", "scanner.py"],
            "transitive": ["pipeline.py", "state.py"],
            "affected_files": 4,
            "risk_level": "low",
            "impact_tree": {"scanner.py": 1, "embedder.py": 1, ...}
        }
    """
    if isinstance(graph, GraphRuntime):
        file_graph = graph.file_graph
        idx = graph._find_vertex(file_graph, target_file)
        if idx is None:
            return {
                "file": target_file,
                "direct": [],
                "transitive": [],
                "affected_files": 0,
                "risk_level": "none",
                "impact_tree": {},
            }
        
        # distances = list of floats, one per vertex in the graph.
        # distances[i] = shortest path distance from config.py to vertex i.
        # mode="in" = reverse direction (who imports me, not who I import).
        # float("inf") = unreachable (no import chain connects them).
        distances = file_graph.shortest_paths(source=idx, mode="in")[0]

        # Build impact_tree: {filename: distance} for all reachable vertices
        # e.g. {"scanner.py": 1, "pipeline.py": 2, "state.py": 3}
        impact_tree = {}

        for vertex_index, distance in enumerate(distances):
            # vertex_index = integer position in the graph (0, 1, 2, ...)
            # distance = how many import hops away (1.0 = direct, 2.0 = transitive)
            # Skip self (vertex_index == idx) and unreachable (inf)
            if vertex_index == idx or distance == float("inf"):
                continue
            # file_graph.vs[vertex_index]["name"] = actual filename like "scanner.py"
            impact_tree[file_graph.vs[vertex_index]["name"]] = int(distance)
        
        # Split into direct (depth 1) and transitive (depth 2+)
        direct = sorted(file for file, distance in impact_tree.items() if distance == 1)
        transitive = sorted(file for file, distance in impact_tree.items() if distance > 1)
        total_affected = len(impact_tree)
        risk_level = _calculate_risk(total_affected, file_graph.vcount())

        return {
            "file": target_file,
            "direct": direct,
            "transitive": transitive,
            "affected_files": total_affected,
            "risk_level": risk_level,
            "impact_tree": impact_tree,
        }
    
    else:
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

        visited = {target_file}
        queue = deque()
        impact_tree = {}

        for dependent in reverse.get(target_file, []):
            if dependent not in visited:
                queue.append((dependent, 1))
                visited.add(dependent)

        while queue:
            current_file, depth = queue.popleft()
            impact_tree[current_file] = depth

            for dependent in reverse.get(current_file, []):
                if dependent not in visited:
                    queue.append((dependent, depth + 1))
                    visited.add(dependent)

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

    EXAMPLES:
        _calculate_risk(4, 56)   # ratio = 0.07 (7%), affected = 4
                                 # 4 >= 4 → "moderate"

        _calculate_risk(25, 100) # ratio = 0.25 (25%), affected = 25
                                 # 25 >= 20 → "critical"

        _calculate_risk(2, 50)   # ratio = 0.04 (4%), affected = 2
                                 # 2 < 4 → "low"

        _calculate_risk(0, 50)   # ratio = 0.0, affected = 0 → "low"
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
    """Blast radius for EVERY file. Ranked by risk.

    source: GraphRuntime (igraph) or dict (legacy BFS).
    """
    if isinstance(graph, GraphRuntime):
        file_graph = graph.file_graph
        total_files = file_graph.vcount()
        if total_files == 0:
            return {
                "blast_map": [],
                "stats": {
                    "total_files": 0, "critical_files": 0,
                    "high_files": 0, "moderate_files": 0, "low_files": 0,
                },
                "highest_risk": "",
            }
        
        all_distance = file_graph.shortest_paths(mode="in")
        results = []
        risk_counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0, "none": 0}

        for index in range(total_files):
            distances = all_distance[index]
            direct_count = 0
            total_affected = 0

            for vertex_index, distance in enumerate(distances):
                if vertex_index == index or distance == float("inf"):
                    continue
                total_affected += 1
                if distance == 1:
                    direct_count += 1
            
            risk_level = _calculate_risk(total_affected, total_files)
            results.append({
                "file": file_graph.vs[index]["name"],
                "affected_files": total_affected,
                "risk_level": risk_level,
                "direct_count": direct_count,
                "transitive_count": total_affected - direct_count,
            })
            risk_counts[risk_level] += 1
        
        results.sort(key=lambda x: x["affected_files"], reverse=True)
        highest_risk = results[0]["file"] if results else ""

        return {
            "blast_map": results,
            "stats": {
                "total_files": total_files,
                "critical_files": risk_counts["critical"],
                "high_files": risk_counts["high"],
                "moderate_files": risk_counts["moderate"],
                "low_files": risk_counts["low"],
            },
            "highest_risk": highest_risk,
        }
    
    else:
        reverse = build_reverse_graph(graph)
        internal_files = set(graph.keys())
        total_files = len(internal_files)

        results = []
        risk_counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0, "none": 0}

        for target_file in graph:
            # BFS from target_file through the pre-built reverse graph
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
