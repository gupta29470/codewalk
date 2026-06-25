"""Indexing pipeline: scan → chunk → embed → store, plus incremental reindex and manifest management."""
import time
import logging
import sys
from pathlib import Path
import threading
import queue
import os
import json
from datetime import datetime, timezone
from pathlib import Path as _Path

from src.codewalk.embeddings.chunker import file_hash, read_file_content
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.embeddings.chunker import chunk_file
from src.codewalk.embeddings.embedder import embed_chunks
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.log import log as _log
from src.codewalk import __version__ as CODEWALK_VERSION

_SENTINEL = object()
EMBED_BATCH_SIZE = 128

logger = logging.getLogger("codewalk")


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


def chunk_and_embed_parallel(files: list[dict]) -> tuple[list[dict], int]:
    """Parallel chunk + embed using producer-consumer threads.

    Args:
        files: List of file_info dicts from scan_directory().

    Returns:
        (embedded_chunks, chunk_count) — the embedded chunk list and
        total number of raw chunks produced.
    """
    chunk_queue_inner = queue.Queue(maxsize=10)
    all_embedded = []
    chunk_count = [0]
    errors = []
    stop_event = threading.Event()

    def producer():
        try:
            batch = []
            total = len(files)
            for i, file_info in enumerate(files, 1):
                if stop_event.is_set():
                    _log("[producer] Stop event set — aborting")
                    break
                chunks = chunk_file(file_info)
                batch.extend(chunks)
                chunk_count[0] += len(chunks)
                if len(batch) >= EMBED_BATCH_SIZE:
                    chunk_queue_inner.put(batch)
                    _log(f"[producer] Chunked {i}/{total} files, queued batch ({len(batch)} chunks)")
                    batch = []
                if i % 100 == 0:
                    _log(f"[producer] Progress: {i}/{total} files chunked")
            if batch and not stop_event.is_set():
                chunk_queue_inner.put(batch)
                _log(f"[producer] Final batch queued ({len(batch)} chunks)")
        except Exception as e:
            errors.append(f"Producer error: {e}")
            _log(f"[producer] ERROR: {e}")
        finally:
            chunk_queue_inner.put(_SENTINEL)
            _log(f"[producer] Done. Total chunks: {chunk_count[0]}")

    def consumer():
        try:
            while True:
                batch = chunk_queue_inner.get()
                if batch is _SENTINEL:
                    _log("[consumer] Received sentinel, stopping")
                    break
                embedded = embed_chunks(batch)
                all_embedded.extend(embedded)
                _log(f"[consumer] Embedded batch ({len(embedded)} chunks, {len(all_embedded)} total)")
        except Exception as e:
            errors.append(f"Consumer error: {e}")
            _log(f"[consumer] ERROR: {e}")
            stop_event.set()

    p = threading.Thread(target=producer, name="chunk-producer", daemon=True)
    c = threading.Thread(target=consumer, name="embed-consumer", daemon=True)
    _log(f"[parallel] Starting producer + consumer threads for {len(files)} files")
    t0 = time.time()
    p.start()
    c.start()
    p.join()
    c.join()
    _log(f"[parallel] Both threads done in {time.time() - t0:.1f}s")

    if errors:
        _log(f"[parallel] Errors: {errors}")
        raise RuntimeError(f"Pipeline errors: {'; '.join(errors)}")

    return all_embedded, chunk_count[0]

def full_index_parallel(repo_path: str = "", collection_name: str = "codebase",
                        persist_dir: str = "./data/chroma", team_config=None) -> dict:
    """Full pipeline: scan → chunk → embed → store. Nukes old data first.

    Args:
        team_config: If provided, uses team_scan_directory (exclude-only).
                     If None, uses scan_directory (file_filter defaults).
    """
    if not repo_path:
        raise ValueError("repo_path is required")
    _log(f"[parallel] Starting: {repo_path}")
    pipeline_start = time.time()

    tech_stack = detect_tech_stack(repo_path)
    _log(f"[parallel] Tech stack: {tech_stack}")

    if team_config:
        from src.codewalk.team_config import team_scan_directory
        files = team_scan_directory(repo_path, team_config)
    else:
        files = scan_directory(repo_path)
    _log(f"[parallel] Scanned {len(files)} files")

    all_embedded, total_chunks = chunk_and_embed_parallel(files)

    _log(f"[parallel] Storing {len(all_embedded)} chunks in ChromaDB...")
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    store.clear_collection()
    store.add_parent_child_chunks(all_embedded)
    index_dir = f"{repo_path.rstrip('/')}/.codewalk"
    write_manifest(
        index_dir,
        file_count=len(files),
        chunk_count=store.chunk_count(),
        collection_name=collection_name,
        index_version=_next_index_version(index_dir),
    )

    total_time = time.time() - pipeline_start
    _log(f"[parallel] Complete: {len(all_embedded)} chunks in {total_time:.1f}s")

    return {
        "repo_path": repo_path,
        "tech_stack": tech_stack,
        "files_scanned": len(files),
        "chunks_created": total_chunks,
        "chunks_embedded": len(all_embedded),
        "embedded_chunks": all_embedded,
        "files": files,
    }

def index_from_paths_parallel(paths: list[str], repo_path: str = "",
                              collection_name: str = "codebase",
                              persist_dir: str = "./data/chroma") -> dict:
    """Index files matching the given paths or directories.

    Accepts both file paths and directory paths. A directory path
    matches all files under it (e.g. "src" matches "src/app/main.py").
    Uses producer-consumer for parallel chunk+embed.
    """
    if not repo_path:
        raise ValueError("repo_path is required")
    pipeline_start = time.time()

    # Step 1: Match files — avoid full repo scan when possible
    t0 = time.time()
    _log("[parallel-paths] Matching files...")
    path_set = set(paths)

    # Fast path: if all paths are individual files, use them directly
    from src.codewalk.ingestion.scanner import detect_language
    files = []
    dirs_to_scan = []
    for path in path_set:
        full = os.path.join(repo_path, path)
        if os.path.isfile(full):
            files.append({
                "file_path": path,
                "absolute_path": full,
                "language": detect_language(__import__("pathlib").Path(full)),
                "size_bytes": os.path.getsize(full),
            })
        else:
            dirs_to_scan.append(path)

    # Only scan repo if we have directory paths or non-existent files
    all_files: list[dict] = []
    seen_paths = {f["file_path"] for f in files}
    if dirs_to_scan:
        all_files = scan_directory(repo_path)
        for file in all_files:
            file_path = file["file_path"]
            if file_path in seen_paths:
                continue
            if file_path in path_set:
                files.append(file)
                seen_paths.add(file_path)
                continue
            for path in path_set:
                if file_path.startswith(path.rstrip("/") + "/"):
                    files.append(file)
                    seen_paths.add(file_path)
                    break
    else:
        all_files = files

    match_time = time.time() - t0
    _log(f"[parallel-paths] Matched {len(files)} files ({match_time:.1f}s)")

    # Step 2: Delete old chunks for matched files to prevent orphans
    t0 = time.time()
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    for file_info in files:
        store.delete_by_file(file_info["file_path"])
    _log(f"[parallel-paths] Deleted old chunks for {len(files)} files ({time.time() - t0:.1f}s)")

    # Step 3: Parallel chunk + embed
    t0 = time.time()
    all_embedded, total_chunks = chunk_and_embed_parallel(files)
    parallel_time = time.time() - t0

    # Step 4: Store
    t0 = time.time()
    store.add_parent_child_chunks(all_embedded)
    index_dir = f"{repo_path.rstrip('/')}/.codewalk"
    write_manifest(
        index_dir,
        file_count=len(files),
        chunk_count=store.chunk_count(),
        collection_name=collection_name,
        index_version=_next_index_version(index_dir),
    )
    store_time = time.time() - t0

    total_time = time.time() - pipeline_start
    _log(f"[parallel-paths] Done: {len(all_embedded)} chunks in {total_time:.1f}s")

    return {
        "repo_path": repo_path,
        "files_scanned": len(files),
        "chunks_created": total_chunks,
        "chunks_embedded": len(all_embedded),
        "embedded_chunks": all_embedded,
        "total_time": f"{total_time:.1f}s",
        "steps": [
                f"Match: {len(files)}/{len(all_files)} files",
                f"Chunk+Embed (parallel): {parallel_time:.1f}s",
                f"Store: {len(all_embedded)} chunks ({store_time:.1f}s)",
            ],
    }



def reindex(repo_path: str = "", collection_name: str = "codebase",
            persist_dir: str = "./data/chroma", team_config=None) -> dict:
    """Smart re-index: only re-embed files that changed, add new, remove deleted.

    Thin wrapper around incremental_reindex() that processes ALL indexed files.
    """
    if not repo_path:
        raise ValueError("repo_path is required")

    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    indexed_files = list(store.get_all_indexed_files())

    # If nothing indexed yet, scan disk for all files
    if not indexed_files:
        if team_config:
            from src.codewalk.team_config import team_scan_directory
            all_files = team_scan_directory(repo_path, team_config)
        else:
            all_files = scan_directory(repo_path)
        indexed_files = [f["file_path"] for f in all_files]

    result = incremental_reindex(indexed_files, repo_path, collection_name, persist_dir=persist_dir, team_config=team_config)

    # Map to return format for /analyze callers
    return {
        "repo_path": result["repo_path"],
        "new_files": result.get("new_files", result["files_reindexed"]),
        "changed_files": result.get("changed_files", result["files_reindexed"]),
        "deleted_files": result["files_deleted"],
        "unchanged_files": result["files_skipped"],
        "files_scanned": result["files_on_disk"],
        "chunks_created": result["chunks_embedded"],
        "chunks_embedded": result["chunks_embedded"],
        "total_time": result["total_time"],
        "embedded_chunks": result.get("embedded_chunks"),
    }

def incremental_reindex(
    paths: list[str],
    repo_path: str = "",
    collection_name: str = "codebase",
    persist_dir: str = "./data/chroma",
    team_config=None,
) -> dict:
    """Incremental reindex — only re-embeds files whose content changed.

    Uses MD5 content hashing stored in ChromaDB chunk metadata to detect
    changes. For each file: hash on disk vs stored hash → skip if equal,
    delete old chunks + re-embed if different, remove if deleted from disk.

    Args:
        paths: File or directory paths to consider for reindexing.
        repo_path: Root of the repository (required).
        collection_name: ChromaDB collection name (default "codebase").
        persist_dir: ChromaDB directory.
        team_config: If provided, uses team_scan_directory instead of scan_directory.

    Returns:
        dict with keys: repo_path, files_on_disk, files_skipped,
        files_reindexed, files_deleted, chunks_embedded, total_time.
    """
    if not repo_path:
        raise ValueError("repo_path is required")
    pipeline_start = time.time()

    # Step 1: Scan disk → match against selected paths
    if team_config:
        from src.codewalk.team_config import team_scan_directory
        all_files = team_scan_directory(repo_path, team_config)
    else:
        all_files = scan_directory(repo_path)

    # If the repo root itself is in the path list, include every scanned file.
    # This lets callers pass [repo_path] to pick up new files as well as changed ones.
    repo_root_resolved = Path(repo_path).resolve()
    include_all = any(
        Path(p).resolve() == repo_root_resolved or p in ("", ".")
        for p in paths
    )

    if include_all:
        disk_files = all_files
    else:
        path_set = set(paths)
        disk_files = []
        for file in all_files:
            file_path = file["file_path"]
            if file_path in path_set:
                disk_files.append(file)
                continue
            for path in path_set:
                if file_path.startswith(path.rstrip("/") + "/"):
                    disk_files.append(file)
                    break

    # Step 2: Open existing collection (DON'T recreate — that wipes it)
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)  # get_or_create — safe

    # Step 3: Get all indexed files + their hashes
    indexed_files = store.get_all_indexed_files()

    # Step 4: Classify each disk file
    to_embed = []    # new or changed
    skipped = 0
    new_files = 0
    changed_files = 0
    disk_paths = set()

    for file_info in disk_files:
        file_path = file_info["file_path"]
        disk_paths.add(file_path)

        content = read_file_content(file_info["absolute_path"]) or ""
        if not content.strip():
            skipped += 1
            continue

        current_hash = file_hash(content)
        stored_hash = store.get_file_hash(file_path)

        if current_hash == stored_hash:
            skipped += 1
        else:
            # Changed or new → delete old chunks first, then re-embed
            if file_path in indexed_files:
                store.delete_by_file(file_path)
                changed_files += 1
            else:
                new_files += 1
            to_embed.append(file_info)
    
    # Step 5: Delete chunks for files removed from disk
    deleted = 0
    for indexed_file_path in indexed_files:
        if indexed_file_path not in disk_paths:
            store.delete_by_file(indexed_file_path)
            deleted += 1
    
    # Step 6: Chunk + embed only the changed/new files
    embedded_count = 0
    all_embedded = []
    if to_embed:
        all_embedded, total_chunks = chunk_and_embed_parallel(to_embed)
        store.add_parent_child_chunks(all_embedded)
        embedded_count = len(all_embedded)

    index_dir = f"{repo_path.rstrip('/')}/.codewalk"
    write_manifest(
        index_dir,
        file_count=len(disk_files),
        chunk_count=store.chunk_count(),
        collection_name=collection_name,
        index_version=_next_index_version(index_dir),
    )

    total_time = time.time() - pipeline_start

    return {
        "repo_path": repo_path,
        "files_on_disk": len(disk_files),
        "files_skipped": skipped,
        "files_reindexed": len(to_embed),
        "new_files": new_files,
        "changed_files": changed_files,
        "files_deleted": deleted,
        "chunks_embedded": embedded_count,
        "embedded_chunks": all_embedded,
        "total_time": f"{total_time:.1f}s",
    }

def write_manifest(
    index_dir: str,
    file_count: int,
    chunk_count: int = 0,
    repo_name: str = "",
    collection_name: str = "",
    commit_sha: str = "",
    commit_message: str = "",
    branch: str = "",
    index_version: int = 1,
    embedding_model: str = "",
    graph_version: str = "1.0",
    minimum_mcp_version: str = "1.0.0",
):
    """Write manifest.json into index_dir (.codewalk/ or incoming/).

    Single source of truth for index metadata — used by:
      - Local indexing (file_count + chunk_count, commit fields empty)
      - Cloud worker (all fields populated after git clone)
      - MCP download_cloud_index_if_missing (reads index_version to check staleness)
      - state._check_upgrade_banner (reads codewalk_version)
    """
    manifest = {
        "codewalk_version": CODEWALK_VERSION,
        "index_version": index_version,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "file_count": file_count,
        "chunk_count": chunk_count,
        "repo": repo_name,
        "collection_name": collection_name,
        "commit_sha": commit_sha,
        "commit_message": commit_message,
        "branch": branch,
        "embedding_model": embedding_model,
        "graph_version": graph_version,
        "minimum_mcp_version": minimum_mcp_version,
    }
    out = _Path(index_dir) / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(manifest, f, indent=2)
    _log(f"[manifest] Wrote {out} (index v{index_version}, codewalk v{CODEWALK_VERSION})")


def build_full_analysis(
    db_path: str,
    files: list[dict],
    embedded_chunks: list[dict] | None = None,
    guidelines_path: str = "",
    docs_path: str = "",
    force_reindex_extras: bool = False,
    repo_path: str = "",
    repo_name: str = "",
    collection_name: str | None = None,
) -> dict:
    """Stateless analysis: deps → modules → DuckDB → knowledge-graph → docs → guidelines.

    Caller must scan first (scan_directory or team_scan_directory) and pass files.
    Shared by CLI, worker, MCP, and API — zero duplication.

    Args:
        db_path:               Path to graph.duckdb.
        files:                 Scanned file list (required).
        embedded_chunks:       Optional chunks from ChromaDB indexing.
        guidelines_path:       Folder with .md/.txt/.rst guidelines (absolute).
        docs_path:             Folder with .md/.pdf/.txt docs (absolute).
        force_reindex_extras:  If True, wipe + re-embed docs/guidelines.
        repo_path:             Repo root path (defaults to parent of db_path's parent).
        repo_name:             Repo name (defaults to repo_path basename).
        collection_name:       Code collection name used for this index; doc collection
                               becomes ``{collection_name}_docs``. Falls back to repo_name.

    Returns: {"files", "deps", "modules_result", "knowledge_graph_path",
              "docs_indexed", "guidelines_indexed"}
    """
    from src.codewalk.analysis.dependency_graph import build_dependency_graph
    from src.codewalk.analysis.module_detector import detect_modules
    from src.codewalk.graph.graph_store import GraphStore
    from src.codewalk.graph.knowledge_graph_export import export_knowledge_graph_from_store

    deps = build_dependency_graph(files)
    modules_result = detect_modules(files, deps)

    graph_store = GraphStore(db_path)
    graph_store.populate_from_analysis(
        files, deps, modules_result,
        embedded_chunks=embedded_chunks,
    )

    index_dir = str(Path(db_path).parent)
    derived_repo_path = repo_path or str(Path(db_path).parent.parent)
    derived_repo_name = repo_name or Path(derived_repo_path).name

    try:
        knowledge_graph_path = export_knowledge_graph_from_store(
            graph_store,
            index_dir=index_dir,
            files=files,
            modules_result=modules_result,
            repo_path=derived_repo_path,
            repo_name=derived_repo_name,
        )
        _log(f"[analysis] Knowledge graph: {knowledge_graph_path}")
    except Exception as e:
        _log(f"[analysis] Knowledge graph export failed (non-fatal): {e}")
        knowledge_graph_path = ""

    graph_store.close()

    _log(f"[analysis] {len(files)} files, {len(deps['graph'])} in graph, "
         f"{len(modules_result['modules'])} modules → {db_path}")

    # ── Index docs + guidelines into same ChromaDB (after DuckDB) ──
    chroma_dir = str(Path(db_path).parent / "chroma")
    docs_indexed = None
    guidelines_indexed = None

    if docs_path and os.path.isdir(docs_path):
        from src.codewalk.doc_knowledge.doc_store import DocStore
        doc_col = f"{(collection_name or derived_repo_name)}_docs"
        doc_store = DocStore(persist_dir=chroma_dir, collection_name=doc_col)
        doc_store.create_collection()
        if force_reindex_extras or doc_store.chunk_count() == 0:
            if force_reindex_extras and doc_store.chunk_count() > 0:
                doc_store.clear()
            docs_indexed = doc_store.index_docs(docs_path)
            _log(f"[analysis] Docs indexed: {docs_indexed}")

    return {
        "files": files,
        "deps": deps,
        "modules_result": modules_result,
        "knowledge_graph_path": knowledge_graph_path,
        "docs_indexed": docs_indexed,
        "guidelines_indexed": None,
    }


