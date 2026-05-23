"""
=============================================================================
 graph_runtime.py — In-Memory Graph Engine (igraph / C-speed)
=============================================================================

WHAT THIS FILE DOES:
    1. Loads file-level and module-level edges from DuckDB (GraphStore).
    2. Builds two in-memory igraph instances for fast traversal.
    3. Provides graph algorithms: blast radius, topological sort,
       cycle detection, centrality, shortest path.

HOW IT WORKS:
    DuckDB stores the edges persistently (disk). But querying "who does
    this file affect transitively?" via SQL would be slow (recursive CTEs).
    Instead, we load all edges into igraph — a C library that does graph
    traversal in microseconds, not milliseconds.

    Two separate graphs:
        file_graph   — file-level import edges (pipeline.py → scanner.py)
        module_graph — module-level dependency edges (pipeline → analysis)

REAL-WORLD ANALOGY:
    DuckDB = filing cabinet (permanent, organized, slow to search through).
    igraph = wall-mounted corkboard with strings connecting pins (instant
    visual traversal — follow the strings to see connections).

    On startup: we read the filing cabinet and pin everything to the board.
    On queries: we just follow strings on the board (fast).
    On re-analysis: we tear down the board and rebuild it (milliseconds).

KEY CONCEPTS:
    - mode="in": reverse traversal (who imports ME → blast radius)
    - mode="out": forward traversal (who do I import → dependencies)
    - order=999: unlimited depth (traverse the entire graph)
    - betweenness: how many shortest paths pass THROUGH this node
      (high = bottleneck, changing it breaks many paths)
    - pagerank: importance based on who links to you recursively
      (high = many important files depend on this one)
    - EMPTY GRAPH: when vcount()==0, topological_sort() falls back to
      all files from DuckDB (alphabetical) instead of returning []

WHERE IT'S CALLED:
    - api/state.py → creates GraphRuntime after GraphStore is populated
    - analysis/blast_radius.py → uses get_blast_radius() for fast mode
    - query.py → passes runtime to compute_file_risks()

DEPENDENCIES:
    - igraph: C-speed graph library (pip install python-igraph)
    - graph_store: Provides edges from DuckDB
=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

# logging: Reports graph sizes on build/rebuild
import logging

# Optional: Used for return type hints where a value might be None
from typing import Optional

# igraph: C-speed graph library. Handles graphs with millions of edges
# in milliseconds. We use it for traversal, not storage.
import igraph as ig

from src.codewalk.graph.graph_store import GraphStore

logger = logging.getLogger("codewalk")


# =============================================================================
# GraphRuntime — Fast In-Memory Graph Engine
# =============================================================================

class GraphRuntime:
    """In-memory graph for fast traversal and analysis.

    Loads edges from GraphStore (DuckDB) into igraph (C-speed).
    Rebuilt on every startup — takes milliseconds even for large codebases.

    Two separate graphs:
        file_graph   — file-level import edges (pipeline.py → scanner.py)
        module_graph — module-level dependency edges (pipeline → analysis)

    EMPTY GRAPH FALLBACK:
        If file_graph has 0 vertices (no import edges detected), topological_sort()
        falls back to returning ALL files from DuckDB sorted alphabetically.
        This handles repos where import extraction isn't supported or produces
        no edges — the reading order won't be dependency-aware but won't be empty.
    """

    # ── Constructor ─────────────────────────────────────────────────

    def __init__(self, store: GraphStore):
        """Build igraph instances from DuckDB edges.

        EXAMPLE (codewalk's own src, 56 files):
            store.get_import_edges() returns 125 tuples:
                [("pipeline.py", "scanner.py"), ("pipeline.py", "chunker.py"), ...]

            self.file_graph = igraph.Graph with:
                vcount() = 56 (one vertex per file)
                ecount() = 125 (one edge per import)
                vs[0]["name"] = "pipeline.py"  (vertex 0's name)
                vs[1]["name"] = "scanner.py"   (vertex 1's name)

            store.get_module_dep_edges() returns:
                [("pipeline", "analysis"), ("pipeline", "embeddings"), ...]

            self.module_graph = igraph.Graph with:
                vcount() = 12, ecount() = 18
        """
        self.store = store
        # Build both graphs from DuckDB edges on startup
        self.file_graph: ig.Graph = self._build_graph(store.get_import_edges())
        self.module_graph: ig.Graph = self._build_graph(store.get_module_dep_edges())
        logger.info(
            f"[GraphRuntime] Built file_graph: {self.file_graph.vcount()} vertices, "
            f"{self.file_graph.ecount()} edges | "
            f"module_graph: {self.module_graph.vcount()} vertices, "
            f"{self.module_graph.ecount()} edges"
        )

    # ── Build / Rebuild ────────────────────────────────────────────

    @staticmethod
    def _build_graph(edges: list[tuple[str, str]]) -> ig.Graph:
        """Build a directed igraph from (source, target) tuples.

        TupleList() auto-creates vertices from the string names.
        Empty edge list → empty graph (no crash).

        EXAMPLE:
            edges = [("pipeline.py", "scanner.py"), ("scanner.py", "config.py")]
            graph = Graph.TupleList(edges, directed=True)
            # graph.vcount() = 3  (pipeline.py, scanner.py, config.py)
            # graph.ecount() = 2  (two edges)
            # graph.vs[0]["name"] = "pipeline.py"
            # graph.vs[1]["name"] = "scanner.py"
            # graph.vs[2]["name"] = "config.py"
        """
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

    # ── Graph Algorithms ───────────────────────────────────────────

    def _find_vertex(self, graph: ig.Graph, name: str) -> Optional[int]:
        """Find vertex index by name. Returns None if not in graph.

        igraph stores vertices by integer index. To look up 'pipeline.py',
        we search the 'name' attribute. ValueError means it's not there.

        EXAMPLE:
            _find_vertex(file_graph, "pipeline.py") = 0     (vertex index 0)
            _find_vertex(file_graph, "scanner.py")  = 1     (vertex index 1)
            _find_vertex(file_graph, "nonexistent") = None   (not in graph)
        """
        try:
            return graph.vs.find(name=name).index
        except ValueError:
            return None
        
    def get_blast_radius(self, file_path: str) -> list[str]:
        """All files affected if this file changes (transitive reverse deps).

        mode='in' = REVERSE traversal: who imports me, directly or transitively.
        order=999 = no depth limit (traverse entire dependency tree).
        This is the C-speed equivalent of the legacy dict-based BFS.

        EXAMPLE:
            Graph: pipeline.py → scanner.py → config.py
                   state.py → pipeline.py

            get_blast_radius("config.py"):
              idx = 2  (config.py is vertex 2)
              # mode="in" = who imports me?
              # config.py is imported by scanner.py (depth 1)
              # scanner.py is imported by pipeline.py (depth 2)
              # pipeline.py is imported by state.py (depth 3)
              affected_indices = [2, 1, 0, 3]  (self + all reverse dependents)
              # Filter out self (index 2)
              returns ["scanner.py", "pipeline.py", "state.py"]
        """
        index = self._find_vertex(self.file_graph, file_path)
        if index is None:
            return []
        # order=999 = unlimited depth. mode="in" = reverse (who imports me).
        affected_indices = self.file_graph.neighborhood(index, order=999, mode="in")
        # Remove the file itself from results (index 0 in the list is always self)
        return [
            self.file_graph.vs[index]["name"]
            for index in affected_indices
            if index != index
        ]
    
    def topological_sort(self) -> list[str]:
        """Files in dependency order (leaf dependencies first).

        A topological sort means: if A imports B, B comes first.
        This gives you the ideal READING ORDER — start with files
        that have no dependencies, then work up to files that import them.

        If cycles exist (A→B→A), DAGs can't be topologically sorted.
        Fallback: sort by in-degree (files imported by many come last).

        EXAMPLE (no cycles):
            Graph: pipeline.py → scanner.py → config.py
            topological_sort() = ["config.py", "scanner.py", "pipeline.py"]
            # Read config first (no deps), then scanner, then pipeline

        EXAMPLE (with cycle):
            Graph: A → B → C → A (circular)
            # is_dag() = False → fallback to in-degree sort
            # in-degree: A=1, B=1, C=1 → sorted by degree (all equal)
        """
        if self.file_graph.vcount() == 0:
            # No import edges in igraph — return all files from DuckDB
            rows = self.store.conn.execute("SELECT path FROM files ORDER BY path").fetchall()
            return [row[0] for row in rows]
        if not self.file_graph.is_dag():
            logger.warning("[GraphRuntime] Cycle detected — using in-degree sort fallback")
            degrees = self.file_graph.indegree()
            sorted_indices = sorted(range(len(degrees)), key=lambda i: degrees[i])
            return [self.file_graph.vs[index]["name"] for index in sorted_indices]
        
        sorted_indices = self.file_graph.topological_sorting()
        return [self.file_graph.vs[index]["name"] for index in sorted_indices]
        
    def detect_cycles(self) -> dict:
        """Detect circular dependencies in the file graph.

        Uses igraph's strongly connected components (SCC) algorithm.
        An SCC with >1 vertex = a cycle group (A→B→C→A).

        Also finds the minimum feedback arc set — the smallest number
        of edges you'd need to remove to break ALL cycles.

        EXAMPLE:
            Graph: A → B → C → A, D → E (no cycle)
            components = [[A, B, C], [D], [E]]
            # [A,B,C] has len > 1 → it's a cycle group
            # [D] and [E] are single-vertex → not cycles
            cycle_groups = [["A", "B", "C"]]
            feedback_arc_set = [edge_index_of(C→A)]  # removing this breaks the cycle
            edges_to_break = [("C", "A")]
        """
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
        """Top files by betweenness and pagerank.

        Betweenness: How many shortest paths pass THROUGH this file.
            High betweenness = bottleneck. If it breaks, many paths break.

        PageRank: Importance based on who links to you recursively.
            High pagerank = many important files depend on this one.
            Same algorithm Google uses to rank web pages.

        EXAMPLE (codewalk's own src):
            betweenness scores:
              config.py     = 245.3   (many paths go through config)
              pipeline.py   = 180.1   (central orchestrator)
              scanner.py    = 12.0    (leaf-ish, few paths through it)

            pagerank scores:
              config.py     = 0.0842  (most-imported file)
              code_parser.py = 0.0651
              scanner.py    = 0.0423

            returns {
                "betweenness": [
                    {"file": "config.py", "score": 245.3},
                    {"file": "pipeline.py", "score": 180.1},
                    ... top 10
                ],
                "pagerank": [
                    {"file": "config.py", "score": 0.0842},
                    ... top 10
                ]
            }
        """
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
    
    # ── Stats ──────────────────────────────────────────────────────

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
