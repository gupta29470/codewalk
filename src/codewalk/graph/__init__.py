"""
=============================================================================
 graph/ — Persistent Graph Storage & Fast Runtime Layer
=============================================================================

WHAT THIS PACKAGE DOES:
    1. Stores the entire codebase structure in DuckDB (files, imports,
       symbols, call edges, modules) — survives restarts.
    2. Loads those edges into igraph (C-speed) for fast traversal at
       query time (blast radius, topological sort, cycle detection).
    3. Extracts symbol-level call sites from tree-sitter ASTs
       (which function calls which function, at which line).

MODULE MAP:
    graph_store.py      DuckDB persistence — 7 tables, deterministic IDs
    graph_runtime.py    igraph in-memory graphs — fast traversal & analysis
    call_extractor.py   tree-sitter AST walking — symbol→symbol call edges

DATA FLOW:
    scan_directory() → files
    build_dependency_graph() → deps
    detect_modules() → module_results
         ↓
    GraphStore.populate_from_analysis(files, deps, module_results)
         ↓  (writes to DuckDB)
    GraphRuntime(store)
         ↓  (loads edges into igraph)
    blast_radius / reading_order / cycle detection / centrality

REAL-WORLD ANALOGY:
    DuckDB is the filing cabinet (organized, labeled, permanent).
    igraph is the whiteboard (fast sketching, easy to erase and redraw).
    call_extractor is the detective who reads every file and notes down
    "who calls whom" before filing the evidence.
=============================================================================
"""
