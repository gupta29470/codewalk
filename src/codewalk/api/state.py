"""
=============================================================================
 state.py - Global Application State (Singleton)
=============================================================================

WHAT THIS FILE DOES:
    Holds all runtime state for the application as module-level variables.
    This is the SINGLE SOURCE OF TRUTH shared between MCP server and API.

    State includes:
    - VectorStore (ChromaDB connection)
    - LangGraph agent
    - Modules result (from detect_modules)
    - File scan results
    - Dependency graph
    - GraphStore (DuckDB persistent graph)
    - GraphRuntime (igraph in-memory engine for fast queries)

HOW IT WORKS:
    - initialize() sets everything after POST /analyze
    - get_*() functions raise RuntimeError if not yet initialized
    - ensure_initialized() auto-loads from disk if possible
    - refresh() updates analysis without touching embeddings
    - rebuild_analysis_cache() re-scans files, deps, modules + rebuilds graph

WHERE IT'S CALLED:
    - api/main.py: all endpoints use get_store(), get_modules_result(), etc.
    - mcp/server.py: shares the same state module

=============================================================================
"""

import logging
import os

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.agent.graph import create_agent
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.config import settings
from src.codewalk.log import log as _log

from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.graph.graph_runtime import GraphRuntime

logger = logging.getLogger("codewalk")

# =============================================================================
# Module-Level State Variables
# =============================================================================

_store: VectorStore | None = None
_agent = None
_modules_result: dict | None = None
_analyze_result: dict | None = None
_files: list[dict] | None = None       # scan_directory() result
_deps: dict | None = None              # build_dependency_graph() result
_repo_path: str | None = None          # target repo being analyzed
_graph_store: GraphStore | None = None
_graph_runtime: GraphRuntime | None = None


# =============================================================================
# Getters (raise if not initialized)
# =============================================================================

def get_store() -> VectorStore:
    """Get the VectorStore. Raises if not initialized."""
    if _store is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _store


def get_agent():
    """Get the compiled agent. Raises if not initialized."""
    if _agent is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _agent


def get_modules_result() -> dict:
    """Get the modules result. Raises if not initialized."""
    if _modules_result is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _modules_result


def get_analyze_result() -> dict:
    """Get the last analyze result."""
    if _analyze_result is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _analyze_result


def get_files() -> list[dict]:
    """Get cached scan_directory() result."""
    if _files is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _files


def get_deps() -> dict:
    """Get cached build_dependency_graph() result."""
    if _deps is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _deps

def get_graph_runtime() -> GraphRuntime:
    """Get the GraphRuntime (igraph). Raises if not initialized."""
    if _graph_runtime is None:
        raise RuntimeError("No codebase analyzed yet. Call POST /analyze first.")
    return _graph_runtime


def get_graph_store() -> GraphStore | None:
    """Get the GraphStore (DuckDB). Returns None if not initialized."""
    return _graph_store


def initialize(store: VectorStore, agent, modules_result: dict, analyze_result: dict,
               files: list[dict] | None = None, deps: dict | None = None,
               repo_path: str | None = None):
    """Set all state after a successful /analyze.

    EXAMPLE TRACE (after analyzing fatih/color):
        store = VectorStore at .codewalk/chroma/ (348 chunks indexed)
        agent = compiled LangGraph StateGraph
        modules_result = {"modules": {"root": {"files": [...], "file_count": 9}}, ...}
        analyze_result = {"repo_path": "/repos/fatih/color", "files": 9, "chunks": 348}
        files = [{"file_path": "color.go", "language": "go", "absolute_path": "/repos/.../color.go"}, ...]
        deps = {"graph": {"color.go": ["fmt","os","strconv"], ...}, "stats": {"total_edges": 12}}

        db_path = "/repos/fatih/color/.codewalk/graph.duckdb"
        _graph_store = GraphStore(db_path)  → creates/opens DuckDB file
        _graph_store.populate_from_analysis(files, deps, modules_result)
            → inserts 9 files, 12 import edges, 105 symbols, 348 calls into DuckDB
        _graph_runtime = GraphRuntime(_graph_store)
            → builds igraph with 9 vertices, 0 internal edges (all imports are stdlib)
        _agent = create_agent(store, modules_result, ..., graph_runtime=_graph_runtime)
            → re-creates agent with igraph-powered tools
    """
    global _store, _agent, _modules_result, _analyze_result, _files, _deps, _repo_path, _graph_store, _graph_runtime
    _store = store
    _agent = agent
    _modules_result = modules_result
    _analyze_result = analyze_result
    _files = files
    _deps = deps
    if repo_path:
        _repo_path = repo_path

    # GraphStore → DuckDB (persistent). GraphRuntime → igraph (in-memory, fast).
    if files and deps and modules_result:
        repo = _repo_path or settings.repo_path
        db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
        _graph_store = GraphStore(db_path)
        _graph_store.populate_from_analysis(files, deps, modules_result)
        _graph_runtime = GraphRuntime(_graph_store)

        # Recreate agent with graph_runtime so tools get igraph speed
        _agent = create_agent(_store, _modules_result, files=_files, deps=_deps, graph_runtime=_graph_runtime, graph_store=_graph_store)


def refresh(files: list[dict], deps: dict, modules_result: dict):
    """Update cached analysis + rebuild graph. Does not re-embed."""
    global _files, _deps, _modules_result, _graph_store, _graph_runtime
    _files = files
    _deps = deps
    _modules_result = modules_result

    # Rebuild graph so blast radius / reading order use fresh data
    repo = _repo_path or settings.repo_path
    db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
    _graph_store = GraphStore(db_path)
    _graph_store.populate_from_analysis(files, deps, modules_result)
    _graph_runtime = GraphRuntime(_graph_store)


# =============================================================================
# Helper Functions
# =============================================================================

def get_repo_path() -> str:
    """Return the current repo path (from state or settings fallback)."""
    return _repo_path or settings.repo_path


def get_collection_name() -> str:
    """Derive ChromaDB collection name from repo path (last segment)."""
    path = _repo_path or settings.repo_path
    return path.rstrip("/").split("/")[-1] or "codebase"


def chroma_path() -> str:
    """ChromaDB directory: {repo_path}/.codewalk/chroma/"""
    repo = (_repo_path or settings.repo_path).rstrip("/")
    return f"{repo}/.codewalk/chroma"


def rebuild_analysis_cache():
    """Re-scan files and rebuild dependency graph + modules. No re-embedding.

    EXAMPLE TRACE (codewalk's own src):
        repo_path = "/Users/amadhavl/Development/codewalk"
        _files = scan_directory(repo_path)  → 56 files
        _deps = build_dependency_graph(_files)
            → {"graph": {"pipeline.py": ["scanner.py","config.py",...], ...}, "stats": {"total_edges": 125}}
        _modules_result = detect_modules(_files, _deps)
            → {"modules": {"analysis": {...}, "embeddings": {...}, ...}, "stats": {"total_modules": 12}}

        GraphStore creates/overwrites graph.duckdb with fresh data
        GraphRuntime builds igraph from new DuckDB edges
    """
    global _files, _deps, _modules_result, _repo_path, _graph_store, _graph_runtime
    repo_path = _repo_path or settings.repo_path
    _repo_path = repo_path
    _files = scan_directory(repo_path)
    _deps = build_dependency_graph(_files)
    _modules_result = detect_modules(_files, _deps)
    _log(f"[cache] Rebuilt: {len(_files)} files, {len(_deps['graph'])} in graph, "
         f"{len(_modules_result['modules'])} modules")
    
    db_path = f"{repo_path.rstrip('/')}/.codewalk/graph.duckdb"
    _graph_store = GraphStore(db_path)
    _graph_store.populate_from_analysis(_files, _deps, _modules_result)
    _graph_runtime = GraphRuntime(_graph_store)


def ensure_initialized():
    """Auto-load index + analysis cache from disk if not already in memory.

    Called by query endpoints so users don't have to manually run
    /analyze after a server restart.

    EXAMPLE TRACE (server restarted, fatih/color was previously analyzed):
        _store = None, _modules_result = None  → not initialized
        chroma = "/repos/fatih/color/.codewalk/chroma"  → exists on disk

        rebuild_analysis_cache()  → re-scans 9 files, rebuilds graph
        _store = VectorStore(persist_dir=chroma)  → loads existing ChromaDB
        _store.create_collection("color")  → opens existing collection
        count = 348  → chunks already in ChromaDB

        _agent = create_agent(...)  → agent with full tools
        # Now all endpoints work without needing POST /analyze
    """
    global _store, _agent, _analyze_result, _graph_store, _graph_runtime 

    if _store is not None and _modules_result is not None:
        return  # Already initialized

    chroma = chroma_path()
    if not os.path.isdir(chroma):
        return  # No index on disk

    _log("[ensure_initialized] Auto-loading index + analysis from disk...")
    rebuild_analysis_cache()

    repo = get_repo_path()
    db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
    _graph_store = GraphStore(db_path)
    _graph_runtime = GraphRuntime(_graph_store)

    _store = VectorStore(persist_dir=chroma)
    _store.create_collection(get_collection_name())

    count = _store.collection.count()
    _log(f"[ensure_initialized] Loaded {count} chunks from {chroma}")

    _analyze_result = {"repo_path": get_repo_path(), "skipped": True}

    if _agent is None and _store is not None and _modules_result is not None:
        _agent = create_agent(_store, _modules_result, files=_files, deps=_deps, graph_runtime=_graph_runtime, graph_store=_graph_store)
        _log("[ensure_initialized] Agent recreated")
        
