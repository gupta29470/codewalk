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

HOW IT WORKS:
    - initialize() sets everything after POST /analyze
    - get_*() functions raise RuntimeError if not yet initialized
    - ensure_initialized() auto-loads from disk if possible
    - refresh() updates analysis without touching embeddings

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


# =============================================================================
# Setters
# =============================================================================

def initialize(store: VectorStore, agent, modules_result: dict, analyze_result: dict,
               files: list[dict] | None = None, deps: dict | None = None,
               repo_path: str | None = None):
    """Set all state after a successful /analyze."""
    global _store, _agent, _modules_result, _analyze_result, _files, _deps, _repo_path
    _store = store
    _agent = agent
    _modules_result = modules_result
    _analyze_result = analyze_result
    _files = files
    _deps = deps
    if repo_path:
        _repo_path = repo_path


def refresh(files: list[dict], deps: dict, modules_result: dict):
    """Update cached analysis without touching store or agent."""
    global _files, _deps, _modules_result
    _files = files
    _deps = deps
    _modules_result = modules_result


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
    """Re-scan files and rebuild dependency graph + modules. No re-embedding."""
    global _files, _deps, _modules_result, _repo_path
    repo_path = _repo_path or settings.repo_path
    _repo_path = repo_path
    _files = scan_directory(repo_path)
    _deps = build_dependency_graph(_files)
    _modules_result = detect_modules(_files, _deps)
    _log(f"[cache] Rebuilt: {len(_files)} files, {len(_deps['graph'])} in graph, "
         f"{len(_modules_result['modules'])} modules")


def ensure_initialized():
    """Auto-load index + analysis cache from disk if not already in memory.

    Called by query endpoints so users don't have to manually run
    /analyze after a server restart.
    """
    global _store, _agent, _analyze_result

    if _store is not None and _modules_result is not None:
        return  # Already initialized

    chroma = chroma_path()
    if not os.path.isdir(chroma):
        return  # No index on disk

    _log("[ensure_initialized] Auto-loading index + analysis from disk...")
    rebuild_analysis_cache()

    _store = VectorStore(persist_dir=chroma)
    _store.create_collection(get_collection_name())

    count = _store.collection.count()
    _log(f"[ensure_initialized] Loaded {count} chunks from {chroma}")

    _analyze_result = {"repo_path": get_repo_path(), "skipped": True}

    if _agent is None and _store is not None and _modules_result is not None:
        _agent = create_agent(_store, _modules_result, files=_files, deps=_deps)