from collections import deque
import math

from src.codewalk.graph.graph_runtime import GraphRuntime

def build_reverse_graph(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    """Reverse the dependency graph: edges go from 'imported' → 'importer'.

    Forward:  pipeline.py → [scanner.py, chunker.py]  (pipeline imports them)
    Reversed: scanner.py → [pipeline.py]              (scanner is imported BY pipeline)
    """
    internal_files = set(graph.keys())
    reverse = {file: [] for file in internal_files}

    for file, deps in graph.items():
        for dep in deps:
            if dep in internal_files:
                reverse[dep].append(file)

    return reverse

def get_blast_radius(target_file: str, graph: dict[str, list[str]]) -> dict:
    """Calculate blast radius for a single file.

    source: GraphRuntime (igraph, C-speed) or dict (legacy Python BFS).
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
        
        distances = file_graph.shortest_paths(source=idx, mode="in")[0]

        impact_tree = {}

        for vertex_index, distance in enumerate(distances):
            if vertex_index == idx or math.isinf(distance):
                continue
            impact_tree[file_graph.vs[vertex_index]["name"]] = int(distance)
        
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



def _calculate_risk(affected: int, total: int) -> str:
    """Risk level based on affected count + ratio to total files.

    critical — >50% OR 20+ files
    high     — >25% OR 10+ files
    moderate — >10% OR 4+ files
    low      — everything else
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
                if vertex_index == index or math.isinf(distance):
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
