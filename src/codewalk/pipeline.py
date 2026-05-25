import time
import logging
import sys
from pathlib import Path
import threading
import queue
import os
import time

from src.codewalk.embeddings.chunker import file_hash, read_file_content
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.embeddings.chunker import chunk_file
from src.codewalk.embeddings.embedder import embed_chunks
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.analysis.relevance_filter import filter_files_with_llm
from src.codewalk.config import settings
from src.codewalk.log import log as _log

CODEWALK_VERSION = "1.13.0"

_SENTINEL = object()
EMBED_BATCH_SIZE = 256

logger = logging.getLogger("codewalk")


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
                        use_llm_filter: bool = True,
                        persist_dir: str = "./data/chroma") -> dict:
    """Full pipeline: scan → chunk → embed → store. Nukes old data first.

    Overlaps chunking (CPU) with embedding (GPU) using a producer-consumer
    pattern with threads.
    """
    repo_path = repo_path or settings.repo_path
    _log(f"[parallel] Starting: {repo_path}")
    pipeline_start = time.time()

    tech_stack = detect_tech_stack(repo_path)
    _log(f"[parallel] Tech stack: {tech_stack}")

    files = scan_directory(repo_path)
    if use_llm_filter:
        files = filter_files_with_llm(files)
    _log(f"[parallel] Scanned {len(files)} files")

    all_embedded, total_chunks = chunk_and_embed_parallel(files)

    _log(f"[parallel] Storing {len(all_embedded)} chunks in ChromaDB...")
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    store.clear_collection()
    store.add_parent_child_chunks(all_embedded)
    _write_meta(repo_path, len(files)) 

    total_time = time.time() - pipeline_start
    _log(f"[parallel] Complete: {len(all_embedded)} chunks in {total_time:.1f}s")

    return {
        "repo_path": repo_path,
        "tech_stack": tech_stack,
        "files_scanned": len(files),
        "chunks_created": total_chunks,
        "chunks_embedded": len(all_embedded),
        "embedded_chunks": all_embedded,
    }

def index_from_paths_parallel(paths: list[str], repo_path: str = "",
                              collection_name: str = "codebase",
                              persist_dir: str = "./data/chroma") -> dict:
    """Index files matching the given paths or directories.

    Accepts both file paths and directory paths. A directory path
    matches all files under it (e.g. "lib" matches "lib/main.dart").
    Uses producer-consumer for parallel chunk+embed.
    """
    pipeline_start = time.time()
    repo_path = repo_path or settings.repo_path

    # Step 1: Match files (same as original — fast, no parallelism needed)
    t0 = time.time()
    _log("[parallel-paths] Scanning files...")
    all_files = scan_directory(repo_path)

    path_set = set(paths)
    files = []

    for file in all_files:
        file_path = file["file_path"]
        if file_path in path_set:
            files.append(file)
            continue
        for path in path_set:
            if file_path.startswith(path + "/") or file_path.startswith(path.rstrip("/") + "/"):
                files.append(file)
                break

    match_time = time.time() - t0
    _log(f"[parallel-paths] Matched {len(files)} of {len(all_files)} files ({match_time:.1f}s)")

    # Step 2+3: Parallel chunk + embed
    t0 = time.time()
    all_embedded, total_chunks = chunk_and_embed_parallel(files)
    parallel_time = time.time() - t0

    # Step 4: Store
    t0 = time.time()
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    store.add_parent_child_chunks(all_embedded)
    _write_meta(repo_path, len(files))
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
            persist_dir: str = "./data/chroma") -> dict:
    """Smart re-index: only re-embed files that changed, add new, remove deleted.

    Thin wrapper around incremental_reindex() that processes ALL indexed files.
    """
    repo_path = repo_path or settings.repo_path

    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    indexed_files = list(store.get_all_indexed_files())

    # If nothing indexed yet, scan disk for all files
    if not indexed_files:
        all_files = scan_directory(repo_path)
        indexed_files = [f["file_path"] for f in all_files]

    result = incremental_reindex(indexed_files, repo_path, collection_name, persist_dir=persist_dir)

    # Map to legacy return format for /analyze callers
    return {
        "repo_path": result["repo_path"],
        "new_files": result["files_reindexed"],
        "changed_files": result["files_reindexed"],
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
) -> dict:
    """Incremental reindex — only re-embeds files whose content changed.

    Uses MD5 content hashing stored in ChromaDB chunk metadata to detect
    changes. For each file: hash on disk vs stored hash → skip if equal,
    delete old chunks + re-embed if different, remove if deleted from disk.

    Based on the "ChromaDB metadata as document registry" pattern from
    Arpit Bhayani's "What Matters in Production RAG".

    Args:
        paths: File or directory paths to consider for reindexing.
        repo_path: Root of the repository (defaults to settings.repo_path).
        collection_name: ChromaDB collection name (default "codebase").

    Returns:
        dict with keys: repo_path, files_on_disk, files_skipped,
        files_reindexed, files_deleted, chunks_embedded, total_time.
    """
    pipeline_start = time.time()
    repo_path = repo_path or settings.repo_path

    # Step 1: Scan disk → match against selected paths
    all_files = scan_directory(repo_path)
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
    disk_paths = set()

    for file_info in disk_files:
        file_path = file_info["file_path"]
        disk_paths.add(file_path)

        content = read_file_content(file_info["absolute_path"])
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
    
    _write_meta(repo_path, len(disk_files))

    total_time = time.time() - pipeline_start

    return {
        "repo_path": repo_path,
        "files_on_disk": len(disk_files),
        "files_skipped": skipped,
        "files_reindexed": len(to_embed),
        "files_deleted": deleted,
        "chunks_embedded": embedded_count,
        "embedded_chunks": all_embedded,
        "total_time": f"{total_time:.1f}s",
    }

def _write_meta(repo_path: str, file_count: int):
    """Write .codewalk/meta.json after indexing."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    meta_path = f"{repo_path.rstrip('/')}/.codewalk/meta.json"

    meta = {
        "codewalk_version": CODEWALK_VERSION,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "file_count": file_count,
        "has_graph": True,
    }

    Path(meta_path).parent.mkdir(parents=True, exist_ok=True)

    with open(meta_path, "w") as file:
        json.dump(meta, file, indent=2)

    _log(f"[meta] Wrote {meta_path} (v{CODEWALK_VERSION})")








