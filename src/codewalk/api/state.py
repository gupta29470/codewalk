import logging
import os
import threading

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.agent.graph import create_agent
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.config import settings
from src.codewalk.log import log as _log

from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.graph.graph_runtime import GraphRuntime

from src.codewalk.doc_knowledge.doc_store import DocStore

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
_banner_shown = False
_init_lock = threading.Lock()
_state_lock = threading.RLock()  # Guards all state mutations (initialize, refresh, rebuild)
_doc_store: DocStore | None = None
_db = None  # Postgres connection wrapper — cloud mode only
pending_update: str | None = None  # Set by MCP ensure_local_index when remote is newer


class _PgHelper:
    """Thin wrapper around psycopg2 connection for cloud.py/worker convenience.
    Provides fetchone/fetchall/execute with positional $1/$2 params."""

    def __init__(self, conn):
        self._conn = conn

    def _run(self, sql: str, *args):
        """Convert $1/$2 placeholders to %s for psycopg2, execute with args."""
        import re
        converted = re.sub(r'\$(\d+)', '%s', sql)
        cur = self._conn.cursor()
        cur.execute(converted, args if args else None)
        return cur

    def fetchone(self, sql: str, *args):
        cur = self._run(sql, *args)
        return cur.fetchone()

    def fetchall(self, sql: str, *args):
        cur = self._run(sql, *args)
        return cur.fetchall()

    def execute(self, sql: str, *args):
        self._run(sql, *args)


def _init_cloud_tables(conn):
    """Create cloud-mode tables if they don't exist. Idempotent."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS repos (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                full_name       TEXT UNIQUE NOT NULL,
                name            TEXT NOT NULL,
                owner           TEXT NOT NULL,
                clone_url       TEXT NOT NULL,
                branch          TEXT NOT NULL DEFAULT 'master',
                installation_id TEXT NOT NULL DEFAULT '',
                repo_token      TEXT NOT NULL DEFAULT '',
                last_indexed_sha VARCHAR(40) DEFAULT NULL,
                index_status    VARCHAR(20) DEFAULT 'pending',
                index_version   INTEGER DEFAULT 1,
                storage_path    TEXT,
                created_at      TIMESTAMP DEFAULT NOW(),
                updated_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_repos_full_name ON repos(full_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_repos_status ON repos(index_status)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                repo_name       TEXT NOT NULL REFERENCES repos(full_name),
                commit_sha      TEXT NOT NULL,
                commit_message  TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'queued',
                error           TEXT,
                queued_at       TIMESTAMP DEFAULT NOW(),
                started_at      TIMESTAMP,
                finished_at     TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_repo ON jobs(repo_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queued ON jobs(queued_at) WHERE status='queued'")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                event_type      TEXT NOT NULL,
                delivery_id     TEXT,
                repo_full_name  TEXT,
                commit_sha      TEXT,
                payload_size    INTEGER,
                status          TEXT DEFAULT 'received',
                error           TEXT,
                created_at      TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_repo ON webhook_deliveries(repo_full_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_delivery ON webhook_deliveries(delivery_id)")

        conn.commit()


def get_db():
    """Get or create the Postgres connection wrapper (cloud/server mode only).
    Requires DATABASE_URL env var."""
    global _db
    if _db is None:
        import psycopg2
        import psycopg2.extras
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set — required for cloud mode.")
        conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        _init_cloud_tables(conn)
        _db = _PgHelper(conn)
    return _db

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

def get_doc_store() -> DocStore:
    """Get or create the DocStore (lazy init — no analyze needed)."""
    global _doc_store
    if _doc_store is None:
        try:
            col_name = f"{get_collection_name()}_docs"
        except Exception:
            col_name = "codebase_docs"
        _doc_store = DocStore(persist_dir=chroma_path(), collection_name=col_name)
        _doc_store.create_collection()

    return _doc_store


def initialize(store: VectorStore, agent, modules_result: dict, analyze_result: dict,
               files: list[dict] | None = None, deps: dict | None = None,
               repo_path: str | None = None,
               embedded_chunks: list[dict] | None = None,
               guidelines_path: str = "", docs_path: str = ""):
    """Set all state after a successful /analyze."""
    global _store, _agent, _modules_result, _analyze_result, _files, _deps, _repo_path, _graph_store, _graph_runtime
    from src.codewalk.pipeline import build_full_analysis

    with _state_lock:
        _store = store
        _analyze_result = analyze_result
        if repo_path:
            _repo_path = repo_path

        # Build DuckDB + deps + modules + docs + guidelines via shared function
        if files:
            repo = _repo_path or settings.repo_path
            db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
            if _graph_store is not None:
                _graph_store.close()

            result = build_full_analysis(
                db_path=db_path,
                files=files,
                embedded_chunks=embedded_chunks,
                guidelines_path=guidelines_path,
                docs_path=docs_path,
            )
            _files = result["files"]
            _deps = result["deps"]
            _modules_result = result["modules_result"]

            # Reopen for runtime queries + recreate agent
            _graph_store = GraphStore(db_path)
            _graph_runtime = GraphRuntime(_graph_store)
            _agent = create_agent(_store, _modules_result, files=_files, deps=_deps, graph_runtime=_graph_runtime, graph_store=_graph_store)


def refresh(files: list[dict], deps: dict, modules_result: dict):
    """Update cached analysis + rebuild graph. Does not re-embed."""
    global _files, _deps, _modules_result, _graph_store, _graph_runtime
    from src.codewalk.pipeline import build_full_analysis

    with _state_lock:
        repo = _repo_path or settings.repo_path
        db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
        if _graph_store is not None:
            _graph_store.close()

        result = build_full_analysis(db_path=db_path, files=files)
        _files = result["files"]
        _deps = result["deps"]
        _modules_result = result["modules_result"]

        # Reopen for runtime queries
        _graph_store = GraphStore(db_path)
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


def _rebuild_memory_caches():
    """Re-scan files and rebuild in-memory caches only. Does NOT touch DuckDB."""
    global _files, _deps, _modules_result, _repo_path
    repo_path = _repo_path or settings.repo_path
    _repo_path = repo_path
    _files = scan_directory(repo_path)
    _deps = build_dependency_graph(_files)
    _modules_result = detect_modules(_files, _deps)
    _log(f"[cache] Memory caches rebuilt: {len(_files)} files, "
         f"{len(_deps['graph'])} in graph, {len(_modules_result['modules'])} modules")


def rebuild_analysis_cache(embedded_chunks: list[dict] | None = None,
                           guidelines_path: str = "", docs_path: str = "",
                           force_reindex_extras: bool = False):
    """Re-scan files, rebuild in-memory caches, AND repopulate DuckDB.

    Use this when the codebase or index has changed (analyze, reindex, refresh).
    For cold start (no changes), use _load_from_disk() instead.
    """
    global _files, _deps, _modules_result, _repo_path, _graph_store, _graph_runtime
    from src.codewalk.pipeline import build_full_analysis

    with _state_lock:
        repo_path = _repo_path or settings.repo_path
        _repo_path = repo_path
        db_path = f"{repo_path.rstrip('/')}/.codewalk/graph.duckdb"

        if _graph_store is not None:
            _graph_store.close()

        # API/MCP path: scan with file_filter defaults (scan_directory)
        files = scan_directory(repo_path)

        result = build_full_analysis(
            db_path=db_path,
            files=files,
            embedded_chunks=embedded_chunks,
            guidelines_path=guidelines_path,
            docs_path=docs_path,
            force_reindex_extras=force_reindex_extras,
        )
        _files = result["files"]
        _deps = result["deps"]
        _modules_result = result["modules_result"]

        # Reopen GraphStore + runtime for query tools (build_full_analysis closes it)
        _graph_store = GraphStore(db_path)
        _graph_runtime = GraphRuntime(_graph_store)


def ensure_initialized():
    """Auto-load index + analysis cache from disk if not already in memory.

    Called by query tools and API endpoints so users don't have to run
    codewalk_analyze_codebase / POST /analyze manually after a restart.
    """
    global _store, _agent, _analyze_result, _graph_store, _graph_runtime 

    if _store is not None and _modules_result is not None:
        return  # Already initialized

    with _init_lock:
        # Double-check inside lock — another thread may have initialized while we waited.
        if _store is not None and _modules_result is not None:
            return

        _ensure_initialized_locked()


def _ensure_initialized_locked():
    """Inner initialization logic — called under _init_lock."""
    global _store, _agent, _analyze_result, _graph_store, _graph_runtime

    chroma = chroma_path()
    if not os.path.isdir(chroma):
        _log("[ensure_initialized] No index found on disk — skipping auto-load. Call POST /analyze or codewalk_analyze_codebase first.")
        return  # No index on disk — nothing to load

    _log("[ensure_initialized] Auto-loading index + analysis from disk...")

    # Rebuild in-memory caches (files, deps, modules) — fast, no DuckDB writes.
    _rebuild_memory_caches()

    # Open existing DuckDB — DON'T repopulate. Data persists from last analyze/reindex.
    repo = get_repo_path()
    db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
    if _graph_store is not None:
        _graph_store.close()
    _graph_store = GraphStore(db_path)
    _graph_runtime = GraphRuntime(_graph_store)

    _store = VectorStore(persist_dir=chroma)
    _store.create_collection(get_collection_name())

    # Backfill chunks table if empty (bridge between DuckDB symbols and ChromaDB embeddings)
    if _graph_store:
        _graph_store.populate_chunks_from_chromadb(_store)

    count = _store.chunk_count()
    _check_upgrade_banner(get_repo_path())
    _log(f"[ensure_initialized] Loaded {count} chunks from {chroma}")

    # Set _analyze_result so API endpoints can read repo_path
    _analyze_result = {"repo_path": get_repo_path(), "skipped": True}

    # Recreate the agent so /chat works after restart
    if _agent is None and _store is not None and _modules_result is not None:
        _agent = create_agent(_store, _modules_result, files=_files, deps=_deps, graph_runtime=_graph_runtime, graph_store=_graph_store)
        _log("[ensure_initialized] Agent recreated")

def _check_upgrade_banner(repo_path: str):
    """Show one-time upgrade banner if index was built with older codewalk."""
    import json
    global _banner_shown

    if _banner_shown:
        return
    
    meta_path = f"{repo_path.rstrip('/')}/.codewalk/manifest.json"
    if not os.path.exists(meta_path):
        return
    
    try:
        with open(meta_path) as file:
            meta = json.load(file)
    except (json.JSONDecodeError, OSError):
        return
    
    from packaging.version import parse as parse_version
    stored_version = parse_version(meta.get("codewalk_version", "0.0.0"))

    from src.codewalk import __version__
    current_version = parse_version(__version__)

    if stored_version < current_version:
        _log(
            f"\n"
            f"  ⚡ Codewalk v{current_version} — index was built with v{stored_version}\n"
            f"     Run codewalk_analyze_codebase to rebuild with latest features.\n"
        )

    _banner_shown = True


        