"""FastAPI application state and index lifecycle management."""
import json
import logging
import os
import threading

from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.agent.graph import create_agent
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.codewalk_config import load_codewalk_yaml, codewalk_scan_directory
from src.codewalk.analysis.dependency_graph import build_dependency_graph
from src.codewalk.analysis.module_detector import detect_modules
from src.codewalk.config import settings
from src.codewalk.log import log as _log
from src.codewalk.embeddings.chunker import read_file_content

from pathlib import Path as _Path

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
pending_update: str | None = None  # Set by MCP download_cloud_index_if_missing when remote is newer


def _resolve_repo_path(repo_path: str | None = None) -> str:
    """Return the explicitly provided repo path, current session state, or cwd discovery.

    Falls back to discovering the repo root from the current working directory via
    codewalk.yaml. This makes both MCP and API resilient when _repo_path has not
    been explicitly set (e.g., after a server restart or a cloud pull).
    """
    global _repo_path
    if repo_path:
        return repo_path.rstrip("/")
    if _repo_path:
        return _repo_path.rstrip("/")

    # Fallback: discover repo root from cwd without creating files.
    from src.codewalk.repo_discovery import find_repo_root, RepoNotFoundError
    try:
        discovered = find_repo_root()
        _repo_path = str(discovered)
        return _repo_path.rstrip("/")
    except RepoNotFoundError as e:
        raise RuntimeError(
            "repo_path is required but was not provided and no codewalk.yaml was found in cwd"
        ) from e


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


# Thread-local Postgres connection wrapper. psycopg2 connections are NOT thread-safe,
# so each thread that needs cloud DB access gets its own connection.
_db_local = threading.local()


def get_db():
    """Get or create a thread-local Postgres connection wrapper (cloud/server mode only).
    Requires DATABASE_URL env var."""
    helper = getattr(_db_local, "helper", None)
    if helper is None:
        import psycopg2
        import psycopg2.extras
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set — required for cloud mode.")
        conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        _init_cloud_tables(conn)
        helper = _PgHelper(conn)
        _db_local.helper = helper
    return helper

def get_store() -> VectorStore:
    """Get the VectorStore. Loads from disk if needed. Raises if no index."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _store


def get_agent():
    """Get the compiled agent. Loads from disk if needed. Raises if no index."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _agent


def get_modules_result() -> dict:
    """Get the modules result. Loads from disk if needed. Raises if no index."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _modules_result


def get_analyze_result() -> dict:
    """Get the last analyze result. Loads from disk if needed."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _analyze_result


def get_files() -> list[dict]:
    """Get cached file list. Loads from disk if needed."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _files


def get_deps() -> dict:
    """Get cached dependency graph. Loads from disk if needed."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _deps

def get_graph_runtime() -> GraphRuntime:
    """Get the GraphRuntime (igraph). Loads from disk if needed."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)
    return _graph_runtime


def get_graph_runtime_if_ready() -> GraphRuntime | None:
    """Return GraphRuntime only if already initialized; never trigger a cold start."""
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
               docs_path: str = "",
               force_reindex_extras: bool = False):
    """Set all state after a successful /analyze."""
    global _store, _agent, _modules_result, _analyze_result, _files, _deps, _repo_path, _graph_store, _graph_runtime
    from src.codewalk.pipeline import build_full_analysis

    with _state_lock:
        _store = store
        _analyze_result = analyze_result
        if repo_path:
            _repo_path = repo_path

        # Build DuckDB + deps + modules + docs via shared function
        if files:
            repo = _resolve_repo_path()
            db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
            if _graph_store is not None:
                _graph_store.close()

            result = build_full_analysis(
                db_path=db_path,
                files=files,
                embedded_chunks=embedded_chunks,
                docs_path=docs_path,
                force_reindex_extras=force_reindex_extras,
                collection_name=getattr(store, "_collection_prefix", None) or get_collection_name(),
            )
            _files = result["files"]
            _deps = result["deps"]
            _modules_result = result["modules_result"]

            # Reopen for runtime queries + recreate agent
            _graph_store = GraphStore(db_path)
            _graph_runtime = GraphRuntime(_graph_store)
            _agent = create_agent(
                _store, _modules_result, files=_files, deps=_deps,
                graph_runtime=_graph_runtime, graph_store=_graph_store,
                repo_path=repo,
            )


def refresh(files: list[dict], deps: dict, modules_result: dict):
    """Update cached analysis + rebuild graph. Does not re-embed."""
    global _files, _deps, _modules_result, _graph_store, _graph_runtime
    from src.codewalk.pipeline import build_full_analysis

    with _state_lock:
        repo = _resolve_repo_path()
        db_path = f"{repo.rstrip('/')}/.codewalk/graph.duckdb"
        if _graph_store is not None:
            _graph_store.close()

        result = build_full_analysis(
            db_path=db_path,
            files=files,
            collection_name=get_collection_name(),
        )
        _files = result["files"]
        _deps = result["deps"]
        _modules_result = result["modules_result"]

        # Reopen for runtime queries
        _graph_store = GraphStore(db_path)
        _graph_runtime = GraphRuntime(_graph_store)


# ─── Helper functions (shared by MCP + API) ─────────────────────────

INDEX_REQUIRED_API = "No index found. Call POST /analyze first."
INDEX_REQUIRED_MCP = "No index found. Call codewalk_analyze_codebase first."


def set_repo_path(repo_path: str) -> None:
    """Set active repo path for index lookups (MCP workspace / API request)."""
    global _repo_path
    _repo_path = repo_path


def get_repo_path() -> str:
    """Return the current repo path from explicit session state.

    Does NOT fall back to cwd discovery. Callers that need discovery should use
    _resolve_repo_path() or ensure_initialized().
    """
    if _repo_path is None:
        raise RuntimeError("Repo path not set. Call set_repo_path() or POST /analyze first.")
    return _repo_path.rstrip("/")


def get_collection_name() -> str:
    """ChromaDB collection prefix for parents/children collections."""
    return _collection_name_for_path(get_repo_path())


def chroma_path() -> str:
    """ChromaDB directory stored inside the target repo: {repo_path}/.codewalk/chroma/"""
    repo = _resolve_repo_path().rstrip("/")
    return f"{repo}/.codewalk/chroma"


def scan_repo_files(repo_path: str | None = None) -> list[dict]:
    """Scan repo respecting codewalk.yaml (same as cloud indexer).

    Always uses codewalk_scan_directory so both indexing.exclude and indexing.include
    are honored, while the core file_filter.py safety net is still applied.
    """
    path = _resolve_repo_path(repo_path).rstrip("/")
    config = load_codewalk_yaml(path)
    files = codewalk_scan_directory(path, config)
    _log(f"[scan] codewalk_scan: {len(files)} files (codewalk.yaml applied)")
    return files


def _file_is_indexable(file_info: dict) -> bool:
    """Return True if a file has non-empty content that produces chunks.

    Empty and whitespace-only files are intentionally skipped by the chunker,
    so they should not be treated as missing during completeness checks.

    If the absolute path is missing or unreadable, assume the file is indexable
    rather than hiding a real problem.
    """
    path = file_info.get("absolute_path", "")
    if not path:
        return True
    from pathlib import Path as _Path
    try:
        if _Path(path).stat().st_size == 0:
            return False
    except (OSError, FileNotFoundError):
        return True
    content = read_file_content(path) or ""
    return bool(content.strip())


def _rebuild_memory_caches():
    """Re-scan files and rebuild in-memory caches only. Does NOT touch DuckDB."""
    global _files, _deps, _modules_result, _repo_path
    repo_path = _resolve_repo_path()
    _repo_path = repo_path
    _files = scan_repo_files(repo_path)
    _deps = build_dependency_graph(_files)
    _modules_result = detect_modules(_files, _deps)
    _log(f"[cache] Memory caches rebuilt: {len(_files)} files, "
         f"{len(_deps['graph'])} in graph, {len(_modules_result['modules'])} modules")


def _collection_name_for_path(repo_path: str) -> str:
    """Collection prefix for a repo path (used before _repo_path is set)."""
    path = repo_path.rstrip("/")
    folder_name = path.split("/")[-1] or "codebase"
    manifest_path = f"{path}/.codewalk/manifest.json"
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                meta = json.load(f)
            if meta.get("collection_name"):
                return meta["collection_name"]
            if meta.get("commit_sha"):
                return "codebase"
        except (json.JSONDecodeError, OSError):
            pass
    return folder_name


def index_on_disk(repo_path: str | None = None) -> bool:
    """True if .codewalk has both manifest.json and a chroma/ directory.

    graph.duckdb and the knowledge-graph JSON are rebuilt from ChromaDB on every
    analyze/reindex run, so they are NOT part of the existence check. A partial
    index (chroma has chunks but manifest is missing) is intentionally treated
    as incomplete so callers can resume it.
    """
    path = _resolve_repo_path(repo_path).rstrip("/")
    chroma = f"{path}/.codewalk/chroma"
    manifest = f"{path}/.codewalk/manifest.json"

    return os.path.isdir(chroma) and os.path.isfile(manifest)


def index_is_complete(repo_path: str | None = None) -> bool:
    """True if manifest exists and ChromaDB contains every scannable file.

    Always reads from disk so it survives MCP server / IDE restarts.
    """
    path = _resolve_repo_path(repo_path).rstrip("/")
    manifest = f"{path}/.codewalk/manifest.json"
    chroma = f"{path}/.codewalk/chroma"

    if not os.path.isfile(manifest) or not os.path.isdir(chroma):
        return False

    try:
        files = scan_repo_files(path)
    except Exception:
        return False

    expected = {f["file_path"] for f in files}
    if not expected:
        return False

    # Empty/whitespace-only files produce no ChromaDB chunks, so completeness
    # only needs to verify that every indexable file is present.
    indexable = {f["file_path"] for f in files if _file_is_indexable(f)}

    store = VectorStore(persist_dir=chroma)
    store.create_collection(_collection_name_for_path(path))
    indexed = store.get_all_indexed_files()
    return indexable.issubset(indexed)


def analyze_or_reindex_index(
    repo_path: str,
    docs_path: str = "",
    mode: str = "auto",
) -> dict:
    """Scan disk, check index status, or run a build/reindex.

    Reads ChromaDB and manifest from disk every time; does not rely on in-memory
    module state, so it works after an MCP server or IDE restart.

    mode:
      - auto:    no index → full build; partial → status="behind"; complete → ready
      - reindex: always run incremental reindex over all files (changed/new/deleted)
      - full:    wipe chroma + manifest and rebuild from scratch

    Returns a summary dict with keys such as:
      repo_path, files_scanned, files_reindexed, files_skipped, files_deleted,
      chunks_embedded, missing_files, missing_count, total_time, status
    """
    from src.codewalk.pipeline import (
        index_from_paths_parallel,
        incremental_reindex,
        write_manifest,
    )

    global _repo_path, _files, _store

    repo_path = _resolve_repo_path(repo_path).rstrip("/")
    _repo_path = repo_path
    collection = _collection_name_for_path(repo_path)
    chroma = f"{repo_path}/.codewalk/chroma"
    index_dir = f"{repo_path}/.codewalk"

    # 1. Always scan disk first to know what should be indexed.
    files = scan_repo_files(repo_path)
    _files = files
    expected_paths = {f["file_path"] for f in files}
    # Empty/whitespace-only files produce no chunks; exclude them from the
    # missing-files check so a repo full of empty __init__.py files stays "ready".
    indexable_paths = {f["file_path"] for f in files if _file_is_indexable(f)}

    # 2. Open ChromaDB from disk (never use cached _store).
    store = VectorStore(persist_dir=chroma)
    store.create_collection(collection)
    _store = store
    indexed_files = store.get_all_indexed_files()
    chunk_count = store.chunk_count()

    # 3. Read manifest if present.
    manifest_path = f"{index_dir}/manifest.json"
    manifest_exists = os.path.isfile(manifest_path)
    manifest_file_count = 0
    if manifest_exists:
        try:
            with open(manifest_path) as f:
                manifest_file_count = int(json.load(f).get("file_count", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            manifest_exists = False

    # 4. Decide state.
    missing_files = indexable_paths - indexed_files
    extra_files = indexed_files - expected_paths
    is_partial = (
        chunk_count > 0
        and (
            not manifest_exists
            or missing_files
            or extra_files
            or manifest_file_count != len(expected_paths)
        )
    )

    # 5. AUTO mode: report behind or ready, build only when truly empty.
    if mode == "auto":
        if chunk_count == 0:
            # Treat as no index: full build.
            mode = "full"
        elif is_partial:
            _log(f"[analyze_or_reindex] Index behind: {len(indexed_files)}/{len(expected_paths)} files indexed")
            sample = sorted(missing_files)[:10]
            return {
                "repo_path": repo_path,
                "files_scanned": len(files),
                "files_reindexed": 0,
                "files_skipped": 0,
                "files_deleted": 0,
                "chunks_embedded": chunk_count,
                "missing_count": len(missing_files),
                "missing_files": sorted(missing_files),
                "missing_sample": sample,
                "total_time": "0.0s",
                "status": "behind",
            }
        else:
            # Complete index: load without re-embedding.
            _log(f"[analyze_or_reindex] Index complete ({chunk_count} chunks) — loading")
            load_scoped_analysis()
            return {
                "repo_path": repo_path,
                "files_scanned": len(files),
                "files_reindexed": 0,
                "files_skipped": len(files),
                "files_deleted": 0,
                "chunks_embedded": chunk_count,
                "missing_count": 0,
                "missing_files": [],
                "total_time": "0.0s",
                "status": "ready",
            }

    # 6. REINDEX / FULL modes: run the appropriate indexing step.
    built = False
    if mode == "full":
        _log(f"[analyze_or_reindex] Full build: {len(files)} files")
        store.clear_collection()
        index_result = index_from_paths_parallel(
            [f["file_path"] for f in files],
            repo_path,
            collection,
            persist_dir=chroma,
        )
        built = True
    else:
        # mode == "reindex"
        _log(f"[analyze_or_reindex] Reindexing all files for changes")
        config = load_codewalk_yaml(repo_path)
        index_result = incremental_reindex(
            [repo_path],
            repo_path,
            collection,
            persist_dir=chroma,
            codewalk_config=config,
        )

    # 7. Refresh store handle after incremental/index_from_paths may have recreated collections.
    store = VectorStore(persist_dir=chroma)
    store.create_collection(collection)

    # 8. Fetch all chunks and rebuild DuckDB + knowledge graph from the complete index.
    all_chunks = store.get_all_chunks()
    # Full mode wipes everything, so re-index docs too. Incremental reindex only
    # updates code files; docs should be refreshed separately unless the caller
    # explicitly wants them rebuilt.
    force_extras = mode == "full"
    rebuild_analysis_cache(
        embedded_chunks=all_chunks,
        docs_path=docs_path,
        force_reindex_extras=force_extras,
        files=files,
    )

    # 9. Write manifest only after a successful build/reindex.
    write_manifest(
        index_dir,
        file_count=len(files),
        chunk_count=store.chunk_count(),
        collection_name=collection,
        index_version=_next_index_version(index_dir),
    )

    return {
        "repo_path": repo_path,
        "files_scanned": len(files),
        "files_reindexed": index_result.get("files_reindexed", 0),
        "files_skipped": index_result.get("files_skipped", 0),
        "files_deleted": index_result.get("files_deleted", 0),
        "chunks_embedded": index_result.get("chunks_embedded", 0),
        "missing_count": 0,
        "missing_files": [],
        "total_time": index_result.get("total_time", "0.0s"),
        "status": "built" if built else "reindexed",
    }


def _next_index_version(index_dir: str) -> int:
    """Read existing manifest and return the next index_version, or 1."""
    manifest_path = _Path(index_dir) / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                data = json.load(f)
            return int(data.get("index_version", 0)) + 1
        except Exception:
            pass
    return 1


def _wire_query_state():
    """Attach VectorStore + agent after DuckDB/files are ready. No rescan."""
    global _store, _agent, _analyze_result

    repo = get_repo_path()
    _store = VectorStore(persist_dir=chroma_path())
    _store.create_collection(get_collection_name())
    if _graph_store:
        _graph_store.populate_chunks_from_chromadb(_store)
    _analyze_result = {"repo_path": repo, "skipped": True}
    if _store is not None and _modules_result is not None:
        try:
            _agent = create_agent(
                _store, _modules_result, files=_files, deps=_deps,
                graph_runtime=_graph_runtime, graph_store=_graph_store,
                repo_path=repo,
            )
        except Exception as e:
            # Agent creation requires an LLM, which may be unset in some MCP
            # environments. The VectorStore + graph runtime are enough for
            # review/blast-radius tools; chat tools will retry agent creation.
            _log(f"[wire_query_state] Agent creation skipped: {e}")


def load_scoped_analysis():
    """Load team-scoped caches + existing DuckDB/Chroma from disk. Does not re-embed."""
    global _files, _deps, _modules_result, _repo_path, _graph_store, _graph_runtime

    repo_path = _resolve_repo_path()
    _repo_path = repo_path
    _files = scan_repo_files(repo_path)
    _deps = build_dependency_graph(_files)
    _modules_result = detect_modules(_files, _deps)

    db_path = f"{repo_path.rstrip('/')}/.codewalk/graph.duckdb"
    if _graph_store is not None:
        _graph_store.close()
    _graph_store = GraphStore(db_path)
    _graph_runtime = GraphRuntime(_graph_store)

    # If the DuckDB was migrated (old schema dropped) or is otherwise empty,
    # repopulate the file/import/module tables from the in-memory scan before
    # we try to backfill chunks from ChromaDB.
    try:
        file_count = _graph_store.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    except Exception:
        file_count = 0
    if file_count == 0:
        _log("[load_scoped] DuckDB files table empty — repopulating from analysis")
        _graph_store.populate_from_analysis(_files, _deps, _modules_result)

    _wire_query_state()
    _check_upgrade_banner(get_repo_path())
    _log(f"[load_scoped] {len(_files)} scoped files, {_store.chunk_count()} chroma chunks")


def rebuild_analysis_cache(embedded_chunks: list[dict] | None = None,
                           docs_path: str = "",
                           force_reindex_extras: bool = False,
                           files: list[dict] | None = None):
    """Re-scan files (or reuse ``files``), rebuild DuckDB, wire store + agent.

    Pass ``files`` from full_index_parallel to avoid a redundant scan.
    """
    global _files, _deps, _modules_result, _repo_path, _graph_store, _graph_runtime
    from src.codewalk.pipeline import build_full_analysis

    with _state_lock:
        repo_path = _resolve_repo_path()
        _repo_path = repo_path
        db_path = f"{repo_path.rstrip('/')}/.codewalk/graph.duckdb"

        if _graph_store is not None:
            _graph_store.close()

        if files is None:
            files = scan_repo_files(repo_path)

        result = build_full_analysis(
            db_path=db_path,
            files=files,
            embedded_chunks=embedded_chunks,
            docs_path=docs_path,
            force_reindex_extras=force_reindex_extras,
            collection_name=get_collection_name(),
        )
        _files = result["files"]
        _deps = result["deps"]
        _modules_result = result["modules_result"]

        _graph_store = GraphStore(db_path)
        _graph_runtime = GraphRuntime(_graph_store)
        _wire_query_state()


def require_index() -> None:
    """Load index from disk if present. Raises RuntimeError(INDEX_REQUIRED_API) if not."""
    if not ensure_initialized():
        raise RuntimeError(INDEX_REQUIRED_API)


def ensure_initialized() -> bool:
    """Load index from disk if present. Returns True if ready to query."""
    global _store, _modules_result

    if _store is not None and _modules_result is not None and _store.chunk_count() > 0:
        return True

    with _init_lock:
        if _store is not None and _modules_result is not None and _store.chunk_count() > 0:
            return True
        if not index_on_disk():
            _log("[ensure_initialized] No index on disk — call POST /analyze or codewalk_analyze_codebase")
            return False
        _log("[ensure_initialized] Loading index from disk...")
        try:
            load_scoped_analysis()
        except Exception as e:
            _log(f"[ensure_initialized] Failed to load scoped analysis: {e}")
            return False
        ready = _store is not None and _store.chunk_count() > 0
        if ready:
            _log(f"[ensure_initialized] Ready — {_store.chunk_count()} chunks")
        else:
            count = _store.chunk_count() if _store else 0
            _log(f"[ensure_initialized] Index loaded but store is empty ({count} chunks)")
        return ready

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
            f"     Run codewalk_incremental_reindex to sync the index with the latest version.\n"
        )

    _banner_shown = True


        