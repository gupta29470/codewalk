import logging
from typing import Optional

import igraph as ig

from src.codewalk.graph.graph_store import GraphStore

logger = logging.getLogger("codewalk")

class GraphRuntime:
    """In-memory graph for fast traversal and analysis.

    Loads edges from GraphStore (DuckDB) into igraph (C-speed).
    Rebuilt on every startup — takes milliseconds even for large codebases.

    Two separate graphs:
        file_graph   — file-level import edges (pipeline.py → scanner.py)
        module_graph — module-level dependency edges (pipeline → analysis)
    """

    def __init__(self, store: GraphStore):
        self.store = store
        self.file_graph: ig.Graph = self._build_graph(store.get_import_edges())
        self.module_graph: ig.Graph = self._build_graph(store.get_module_dep_edges())
        logger.info(
            f"[GraphRuntime] Built file_graph: {self.file_graph.vcount()} vertices, "
            f"{self.file_graph.ecount()} edges | "
            f"module_graph: {self.module_graph.vcount()} vertices, "
            f"{self.module_graph.ecount()} edges"
        )

    @staticmethod
    def _build_graph(edges: list[tuple[str, str]]) -> ig.Graph:
        """Build a directed igraph from (source, target) tuples."""
        if not edges:
            return ig.Graph(directed=True)
        return ig.Graph.TupleList(edges, directed=True)
    
    def rebuild(self):
        """Rebuild both graphs from DuckDB. Call after re-analysis."""
        self.file_graph = self._build_graph(self.store.get_import_edges())
        self.module_graph = self._build_graph(self.store.get_module_dep_edges())
        logger.info(
            f"[GraphRuntime] Rebuilt file_graph: {self.file_graph.vcount()} vertices, "
            f"{self.file_graph.ecount()} edges | "
            f"module_graph: {self.module_graph.vcount()} vertices, "
            f"{self.module_graph.ecount()} edges"
        )

    def _find_vertex(self, graph: ig.Graph, name: str) -> Optional[int]:
        """Find vertex index by name. Returns None if not in graph."""
        try:
            return graph.vs.find(name=name).index
        except ValueError:
            return None
        
    def get_blast_radius(self, file_path: str) -> list[str]:
        """All files affected if this file changes (transitive reverse deps)."""
        start = self._find_vertex(self.file_graph, file_path)
        if start is None:
            return []
        # order=999 = unlimited depth. mode="in" = reverse (who imports me).
        affected_indices = self.file_graph.neighborhood(start, order=999, mode="in")
        # Remove the file itself from results (index 0 in the list is always self)
        return [
            self.file_graph.vs[idx]["name"]
            for idx in affected_indices
            if idx != start
        ]
    
    def topological_sort(self) -> list[str]:
        """Files in dependency order (leaf dependencies first).

        igraph only contains files that appear in at least one import edge.
        Files with zero import relationships (no imports AND not imported)
        are missing from igraph entirely. We append them at the end from DuckDB
        so the reading order includes ALL indexed files, not just connected ones.
        """
        if self.file_graph.vcount() == 0:
            # No import edges in igraph — return all files from DuckDB
            rows = self.store.conn.execute("SELECT path FROM files ORDER BY path").fetchall()
            return [row[0] for row in rows]
        if not self.file_graph.is_dag():
            logger.warning("[GraphRuntime] Cycle detected — using in-degree sort fallback")
            degrees = self.file_graph.indegree()
            sorted_indices = sorted(range(len(degrees)), key=lambda i: degrees[i])
            sorted_files = [self.file_graph.vs[index]["name"] for index in sorted_indices]
        else:
            # mode="in": for edge A→B (A imports B), B comes first.
            # This puts dependencies before dependents = correct reading order.
            sorted_indices = self.file_graph.topological_sorting(mode="in")
            sorted_files = [self.file_graph.vs[index]["name"] for index in sorted_indices]

        # Append orphan files (in DuckDB but not in igraph — zero import edges)
        all_files = self.store.get_all_files()
        graph_files = set(sorted_files)
        orphans = sorted(f for f in all_files if f not in graph_files)
        if orphans:
            logger.info(
                f"[GraphRuntime] topological_sort: {len(sorted_files)} connected + "
                f"{len(orphans)} orphan files (no import edges)"
            )
        return sorted_files + orphans
        
    def detect_cycles(self) -> dict:
        """Detect circular dependencies in the file graph."""
        if self.file_graph.vcount() == 0:
            return {"has_cycles": False, "cycle_groups": [], "edges_to_break": []}
        
        if self.file_graph.is_dag():
            return {"has_cycles": False, "cycle_groups": [], "edges_to_break": []}
        
        # Find strongly connected components (cycle groups)
        components = self.file_graph.components(mode="STRONG")
        cycle_groups = [
            [self.file_graph.vs[index]["name"] for index in cycle_group]
            for cycle_group in components
            if len(cycle_group) > 1  # single-vertex components aren't cycles
        ]

        # Find minimum edges to remove to break all cycles
        fas = self.file_graph.feedback_arc_set()
        edges_to_break = [
            (
                self.file_graph.vs[self.file_graph.es[e].source]["name"],
                self.file_graph.vs[self.file_graph.es[e].target]["name"],
            )
            for e in fas
        ]

        return {
            "has_cycles": True,
            "cycle_groups": cycle_groups,
            "edges_to_break": edges_to_break,
        }
    
    def centrality(self, top_n: int = 10) -> dict:
        """Top files by betweenness and pagerank."""
        if self.file_graph.vcount() == 0:
            return {"betweenness": [], "pagerank": []}
        
        names = self.file_graph.vs["name"]

        betweenness = self.file_graph.betweenness()
        page_rank = self.file_graph.pagerank()

        # Pair each file with its score, sort descending, take top N
        top_betweenness = sorted(
            zip(names, betweenness), key=lambda x: x[1], reverse=True
        )[:top_n]

        top_pagerank = sorted(
            zip(names, page_rank), key=lambda x: x[1], reverse=True
        )[:top_n]

        return {
            "betweenness": [{"file": f, "score": round(s, 4)} for f, s in top_betweenness],
            "pagerank": [{"file": f, "score": round(s, 6)} for f, s in top_pagerank],
        }
    
    def shortest_path(self, source: str, target: str) -> list[str]:
        """Shortest import chain from source to target file."""
        source_index = self._find_vertex(self.file_graph, source)
        target_index = self._find_vertex(self.file_graph, target)

        if source_index is None or target_index is None:
            return []
        
        paths = self.file_graph.get_shortest_paths(source_index, target_index)
        if not paths or not paths[0]:
            return []
        
        return [self.file_graph.vs[index]["name"] for index in paths[0]]
    
    def get_graph_stats(self) -> dict:
        """Summary stats for both graphs."""
        return {
            "file_graph": {
                "vertices": self.file_graph.vcount(),
                "edges": self.file_graph.ecount(),
                "is_dag": self.file_graph.is_dag() if self.file_graph.vcount() > 0 else True,
            },
            "module_graph": {
                "vertices": self.module_graph.vcount(),
                "edges": self.module_graph.ecount(),
                "is_dag": self.module_graph.is_dag() if self.module_graph.vcount() > 0 else True,
            },
        }
