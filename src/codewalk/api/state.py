from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.agent.graph import create_agent

# ─── Module-level state ─────────────────────────────────────────────

_store: VectorStore | None = None
_agent = None
_modules_result: dict | None = None
_analyze_result: dict | None = None
_files: list[dict] | None = None       # scan_directory() result
_deps: dict | None = None              # build_dependency_graph() result

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


def initialize(store: VectorStore, agent, modules_result: dict, analyze_result: dict,
               files: list[dict] | None = None, deps: dict | None = None):
    """Set all state after a successful /analyze."""
    global _store, _agent, _modules_result, _analyze_result, _files, _deps
    _store = store
    _agent = agent
    _modules_result = modules_result
    _analyze_result = analyze_result
    _files = files
    _deps = deps


def refresh(files: list[dict], deps: dict, modules_result: dict):
    """Update cached analysis without touching store or agent."""
    global _files, _deps, _modules_result
    _files = files
    _deps = deps
    _modules_result = modules_result