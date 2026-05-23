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

# ─── Module-level state (single source of truth for MCP + API) ──────

_store: VectorStore | None = None
_agent = None
_modules_result: dict | None = None
_analyze_result: dict | None = None
_files: list[dict] | None = None       # scan_directory() result
_deps: dict | None = None              # build_dependency_graph() result
_repo_path: str | None = None          # target repo being analyzed
_graph_store: GraphStore | None = None
_graph_runtime: GraphRuntime | None = None

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


def initialize(store: VectorStore, agent, modules_result: dict, analyze_result: dict,
               files: list[dict] | None = None, deps: dict | None = None,
               repo_path: str | None = None):
    """Set all state after a successful /analyze."""
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
        _agent = create_agent(_store, _modules_result, files=_files, deps=_deps, graph_runtime=_graph_runtime)


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


# ─── Helper functions (shared by MCP + API) ─────────────────────────

def get_repo_path() -> str:
    """Return the current repo path (from state or settings fallback)."""
    return _repo_path or settings.repo_path


def get_collection_name() -> str:
    """Derive the ChromaDB collection name from the repo path (last segment).

    e.g. /home/user/my-project  →  "my-project"
         /opt/repos/backend     →  "backend"
    """
    path = _repo_path or settings.repo_path
    return path.rstrip("/").split("/")[-1] or "codebase"


def chroma_path() -> str:
    """ChromaDB directory stored inside the target repo: {repo_path}/.codewalk/chroma/"""
    repo = (_repo_path or settings.repo_path).rstrip("/")
    return f"{repo}/.codewalk/chroma"


def rebuild_analysis_cache():
    """Re-scan files and rebuild dependency graph + modules. No re-embedding."""
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

    Called by query tools and API endpoints so users don't have to run
    codewalk_analyze_codebase / POST /analyze manually after a restart.
    """
    global _store, _agent, _analyze_result, _graph_store, _graph_runtime 

    if _store is not None and _modules_result is not None:
        return  # Already initialized

    chroma = chroma_path()
    if not os.path.isdir(chroma):
        return  # No index on disk — nothing to load

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

    # Set _analyze_result so API endpoints can read repo_path
    _analyze_result = {"repo_path": get_repo_path(), "skipped": True}

    # Recreate the agent so /chat works after restart
    if _agent is None and _store is not None and _modules_result is not None:
        _agent = create_agent(_store, _modules_result, files=_files, deps=_deps, graph_runtime=_graph_runtime)
        _log("[ensure_initialized] Agent recreated")
        