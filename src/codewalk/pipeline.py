"""
=============================================================================
 pipeline.py - The Orchestrator (Scan -> Chunk -> Embed -> Store)
=============================================================================

WHAT THIS FILE DOES:
    Orchestrates the entire indexing pipeline. Takes a repo path and produces
    a searchable vector database. This is the "main function" that ties
    all other modules together.

    THREE PIPELINE MODES:
      1. full_index()          - Nuke everything, index from scratch
      2. index_from_paths()    - Index only specific files/directories
      3. incremental_reindex() - Smart: only re-embed changed files

HOW IT WORKS (full_index):
    1. detect_tech_stack() - identify languages/frameworks (informational)
    2. scan_directory()    - find all source files (uses file_filter)
    3. filter_files_with_llm() - optional LLM filtering of borderline files
    4. chunk_all_files()   - split files into embeddable chunks
    5. embed_chunks()      - convert chunks to vectors (GPU/MPS)
    6. store in ChromaDB   - persist vectors for search

PARALLEL VERSION (full_index_parallel):
    Uses producer-consumer threads:
      - Producer thread: chunks files (CPU-bound, fast)
      - Consumer thread: embeds chunks (GPU-bound, slower)
    Overlapping these saves ~30% total time.

INCREMENTAL REINDEX:
    Compares MD5 hashes of file content:
      - stored_hash (in ChromaDB metadata) vs current_hash (from disk)
      - Match -> skip (no work needed)
      - Different -> delete old chunks + re-embed
      - File deleted from disk -> remove its chunks from ChromaDB

REAL-WORLD ANALOGY:
    Like a search engine indexer (Google's crawler):
      - full_index = crawl the entire internet from scratch
      - incremental_reindex = only re-crawl pages that changed since last time

WHERE IT'S CALLED:
    - server.py -> codewalk_analyze_codebase() and codewalk_index_filtered_files()
    - Can also be called directly from Python

DEPENDENCIES:
    - scanner.py: file discovery
    - tech_detect.py: language detection
    - chunker.py: code splitting
    - embedder.py: vectorization
    - vector_store.py: ChromaDB storage
    - relevance_filter.py: optional LLM filtering

=============================================================================
"""

# --- Imports ---

import time
import logging
import threading
import queue

from src.codewalk.embeddings.chunker import file_hash, read_file_content
from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.embeddings.chunker import chunk_all_files, chunk_file
from src.codewalk.embeddings.embedder import embed_chunks
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.analysis.relevance_filter import filter_files_with_llm
from src.codewalk.config import settings
from src.codewalk.log import log as _log

_SENTINEL = object()  # Marker to signal "queue is done, stop consuming"
EMBED_BATCH_SIZE = 256  # Chunks to accumulate before sending to GPU

logger = logging.getLogger("codewalk")


# =============================================================================
# chunk_and_embed_parallel() - Producer-Consumer Pattern
# =============================================================================

def chunk_and_embed_parallel(files: list[dict]) -> tuple[list[dict], int]:
    """Parallel chunk + embed using producer-consumer threads.

    WHY PARALLEL?
        Chunking is CPU-bound (tree-sitter parsing, text splitting).
        Embedding is GPU-bound (neural network inference on MPS/CUDA).
        They use different hardware -> overlap them for ~30% speedup.

    HOW IT WORKS:
        Producer thread:
          - Iterates through files
          - Chunks each file (CPU)
          - Accumulates chunks until batch reaches 256
          - Puts batch on shared queue

        Consumer thread:
          - Waits for batches on the queue
          - Embeds each batch (GPU)
          - Collects results

        Sentinel pattern:
          - Producer puts _SENTINEL on queue when done
          - Consumer sees _SENTINEL -> stops

    Args:
        files: List of file_info dicts from scan_directory().

    Returns:
        (embedded_chunks, total_chunk_count)
    """
    chunk_queue_inner = queue.Queue(maxsize=10)  # Backpressure: max 10 batches queued
    all_embedded = []
    chunk_count = [0]  # Mutable container so threads can update it
    errors = []

    def producer():
        """Chunks files and puts batches on the queue."""
        try:
            batch = []
            total = len(files)
            for i, file_info in enumerate(files, 1):
                chunks = chunk_file(file_info)
                batch.extend(chunks)
                chunk_count[0] += len(chunks)
                if len(batch) >= EMBED_BATCH_SIZE:
                    chunk_queue_inner.put(batch)
                    _log(f"[producer] Chunked {i}/{total} files, queued batch ({len(batch)} chunks)")
                    batch = []
                if i % 100 == 0:
                    _log(f"[producer] Progress: {i}/{total} files chunked")
            if batch:
                chunk_queue_inner.put(batch)
                _log(f"[producer] Final batch queued ({len(batch)} chunks)")
        except Exception as e:
            errors.append(f"Producer error: {e}")
            _log(f"[producer] ERROR: {e}")
        finally:
            chunk_queue_inner.put(_SENTINEL)
            _log(f"[producer] Done. Total chunks: {chunk_count[0]}")

    def consumer():
        """Takes batches from queue and embeds them (GPU)."""
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

    # Start both threads
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


# =============================================================================
# full_index() - Complete Reindex From Scratch
# =============================================================================

def full_index(repo_path: str = "", collection_name: str = "codebase",
               use_llm_filter: bool = True,
               persist_dir: str = "./data/chroma") -> dict:
    """Full pipeline: scan -> chunk -> embed -> store. Nukes old data first.

    EXECUTION FLOW:
        1. Detect tech stack (languages/frameworks) - informational only
        2. Scan directory - find all source files
        3. Optional LLM filter - remove test/migration/docs files
        4. Chunk all files - split into embeddable pieces
        5. Embed all chunks - vectorize with Jina model
        6. Store in ChromaDB - clear old data, insert new

    Args:
        use_llm_filter: If True, uses LLM to filter files. If False,
                        only uses pattern matching (for MCP mode).
    """
    repo_path = repo_path or settings.repo_path
    _log(f"[full_index] Starting: {repo_path}")

    # Step 1: Detect tech stack (just for info)
    tech_stack = detect_tech_stack(repo_path)
    _log(f"[full_index] Tech stack: {tech_stack}")

    # Step 2: Scan directory
    files = scan_directory(repo_path)
    if use_llm_filter:
        files = filter_files_with_llm(files)
    _log(f"[full_index] Scanned {len(files)} files")

    # Step 3: Chunk all files
    chunks = chunk_all_files(files)
    _log(f"[full_index] Generated {len(chunks)} chunks")

    # Step 4: Embed all chunks
    embedded = embed_chunks(chunks)
    _log(f"[full_index] Embedded {len(embedded)} chunks")

    # Step 5: Store in ChromaDB (nuke old data first)
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    store.clear_collection()  # DELETE everything before inserting
    store.add_chunks(embedded)
    _log(f"[full_index] Stored {len(embedded)} chunks in ChromaDB")

    return {
        "repo_path": repo_path,
        "tech_stack": tech_stack,
        "files_scanned": len(files),
        "chunks_created": len(chunks),
        "chunks_embedded": len(embedded),
    }


# =============================================================================
# full_index_parallel() - Same as full_index but CPU+GPU overlap
# =============================================================================

def full_index_parallel(repo_path: str = "", collection_name: str = "codebase",
                        use_llm_filter: bool = True,
                        persist_dir: str = "./data/chroma") -> dict:
    """Full pipeline with parallel chunking + embedding.

    Same as full_index() but overlaps chunking (CPU) with embedding (GPU)
    using producer-consumer threads. ~30% faster for large repos.
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
    store.add_chunks(all_embedded)

    total_time = time.time() - pipeline_start
    _log(f"[parallel] Complete: {len(all_embedded)} chunks in {total_time:.1f}s")

    return {
        "repo_path": repo_path,
        "tech_stack": tech_stack,
        "files_scanned": len(files),
        "chunks_created": total_chunks,
        "chunks_embedded": len(all_embedded),
    }


# =============================================================================
# index_from_paths_parallel() - Index Specific Paths (Fast)
# =============================================================================

def index_from_paths_parallel(paths: list[str], repo_path: str = "",
                              collection_name: str = "codebase",
                              persist_dir: str = "./data/chroma") -> dict:
    """Parallel version of index_from_paths.

    Same file-matching logic, but uses producer-consumer for chunk+embed.
    Used by MCP tool codewalk_index_filtered_files() after user selects files.
    """
    pipeline_start = time.time()
    repo_path = repo_path or settings.repo_path

    # Step 1: Match files against provided paths
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
        # Also match if file is INSIDE a requested directory
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

    # Step 4: Store (upsert - doesn't delete existing unrelated chunks)
    t0 = time.time()
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    store.add_chunks(all_embedded)
    store_time = time.time() - t0

    total_time = time.time() - pipeline_start
    _log(f"[parallel-paths] Done: {len(all_embedded)} chunks in {total_time:.1f}s")

    return {
        "repo_path": repo_path,
        "files_scanned": len(files),
        "chunks_created": total_chunks,
        "chunks_embedded": len(all_embedded),
        "total_time": f"{total_time:.1f}s",
        "steps": [
            f"Match: {len(files)}/{len(all_files)} files",
            f"Chunk+Embed (parallel): {parallel_time:.1f}s",
            f"Store: {len(all_embedded)} chunks ({store_time:.1f}s)",
        ],
    }


# =============================================================================
# index_from_paths() - Sequential Version (simpler, for debugging)
# =============================================================================

def index_from_paths(paths: list[str], repo_path: str = "",
                     collection_name: str = "codebase",
                     persist_dir: str = "./data/chroma") -> dict:
    """Index files matching the given paths or directories (sequential).

    Accepts both file paths and directory paths. A directory path
    matches all files under it (e.g. "lib" matches "lib/main.dart").
    """
    pipeline_start = time.time()
    steps = []
    repo_path = repo_path or settings.repo_path

    # Step 1: Match files
    t0 = time.time()
    _log("[1/4] Scanning files...")
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

    elapsed = time.time() - t0
    _log(f"[1/4] Matched {len(files)} of {len(all_files)} files ({elapsed:.1f}s)")
    steps.append(f"Scan: {len(files)}/{len(all_files)} files ({elapsed:.1f}s)")

    # Step 2: Chunk
    t0 = time.time()
    _log("[2/4] Chunking files...")
    chunks = chunk_all_files(files)
    elapsed = time.time() - t0
    _log(f"[2/4] Created {len(chunks)} chunks ({elapsed:.1f}s)")
    steps.append(f"Chunk: {len(chunks)} chunks ({elapsed:.1f}s)")

    # Step 3: Embed
    t0 = time.time()
    _log("[3/4] Embedding chunks...")
    embedded = embed_chunks(chunks)
    elapsed = time.time() - t0
    _log(f"[3/4] Embedded {len(embedded)} chunks ({elapsed:.1f}s)")
    steps.append(f"Embed: {len(embedded)} chunks ({elapsed:.1f}s)")

    # Step 4: Store
    t0 = time.time()
    _log("[4/4] Storing in ChromaDB...")
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)
    store.add_chunks(embedded)
    elapsed = time.time() - t0
    _log(f"[4/4] Stored {len(embedded)} chunks ({elapsed:.1f}s)")
    steps.append(f"Store: {len(embedded)} chunks ({elapsed:.1f}s)")

    total_time = time.time() - pipeline_start
    _log(f"Pipeline complete in {total_time:.1f}s")

    return {
        "repo_path": repo_path,
        "files_scanned": len(files),
        "chunks_created": len(chunks),
        "chunks_embedded": len(embedded),
        "total_time": f"{total_time:.1f}s",
        "steps": steps,
    }


# =============================================================================
# reindex() - Smart Re-index (Legacy Wrapper)
# =============================================================================

def reindex(repo_path: str = "", collection_name: str = "codebase",
            persist_dir: str = "./data/chroma") -> dict:
    """Smart re-index: only re-embed files that changed, add new, remove deleted.

    Thin wrapper around incremental_reindex() that processes ALL indexed files.
    Maps the result to legacy return format for backward compatibility.
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

    # Map to legacy return format
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
    }


# =============================================================================
# incremental_reindex() - The Smart One (Hash-Based Change Detection)
# =============================================================================

def incremental_reindex(
    paths: list[str],
    repo_path: str = "",
    collection_name: str = "codebase",
    persist_dir: str = "./data/chroma",
) -> dict:
    """Incremental reindex - only re-embeds files whose content changed.

    ALGORITHM (MD5 hash comparison):
        For each file in paths:
          1. Read file from disk -> compute MD5 hash
          2. Look up stored hash in ChromaDB metadata
          3. If hashes match -> SKIP (file unchanged, save GPU time)
          4. If different -> DELETE old chunks + re-embed fresh
          5. If file deleted from disk -> DELETE its chunks from ChromaDB

    WHY MD5?
        - Fast (microseconds per file)
        - Stored in ChromaDB metadata (no extra DB needed)
        - Collision-resistant enough for change detection
        - NOT for security (but that's fine here)

    PERFORMANCE IMPACT:
        Full reindex of 2000 files: ~5 minutes
        Incremental (3 files changed): ~10 seconds
        The hash comparison turns a 5-minute job into 10 seconds.

    Args:
        paths: File or directory paths to consider for reindexing.
        repo_path: Root of the repository.
        collection_name: ChromaDB collection name.
    """
    pipeline_start = time.time()
    repo_path = repo_path or settings.repo_path

    # Step 1: Scan disk -> match against selected paths
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

    # Step 2: Open existing collection (get_or_create - safe, won't delete)
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection(collection_name)

    # Step 3: Get all indexed files + their stored hashes
    indexed_files = store.get_all_indexed_files()

    # Step 4: Classify each disk file (unchanged/changed/new)
    to_embed = []    # Files that need re-embedding
    skipped = 0      # Files with matching hash (unchanged)
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
            skipped += 1  # File unchanged -> skip
        else:
            # File changed or new -> delete old chunks, then re-embed
            if file_path in indexed_files:
                store.delete_by_file(file_path)
            to_embed.append(file_info)

    # Step 5: Delete chunks for files that were REMOVED from disk
    deleted = 0
    for indexed_file_path in indexed_files:
        if indexed_file_path not in disk_paths:
            store.delete_by_file(indexed_file_path)
            deleted += 1

    # Step 6: Chunk + embed only the changed/new files
    embedded_count = 0
    if to_embed:
        all_embedded, total_chunks = chunk_and_embed_parallel(to_embed)
        store.add_chunks(all_embedded)
        embedded_count = len(all_embedded)

    total_time = time.time() - pipeline_start

    return {
        "repo_path": repo_path,
        "files_on_disk": len(disk_files),
        "files_skipped": skipped,
        "files_reindexed": len(to_embed),
        "files_deleted": deleted,
        "chunks_embedded": embedded_count,
        "total_time": f"{total_time:.1f}s",
    }
