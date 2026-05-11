import time
import logging
import sys
from pathlib import Path
import threading
import queue

from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.embeddings.chunker import chunk_all_files, chunk_file
from src.codewalk.embeddings.embedder import embed_chunks
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.analysis.relevance_filter import filter_files_with_llm
from src.codewalk.config import settings

# Set up file logger — user can tail -f data/codewalk.log
_log_dir = Path("data")
_log_dir.mkdir(exist_ok=True)

_SENTINEL = object()
EMBED_BATCH_SIZE = 256

logger = logging.getLogger("codewalk")
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler(_log_dir / "codewalk.log")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)

def _log(msg: str):
    """Log to both stdout and file."""
    print(msg, file=sys.stderr)
    logger.info(msg)


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

    def producer():
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

def full_index(repo_path: str = "", collection_name: str = "codebase",
               use_llm_filter: bool = True) -> dict:
    """Full pipeline: scan → chunk → embed → store. Nukes old data first.

    Args:
        use_llm_filter: If True, uses LLM to filter files. If False, uses
                        Python pattern matching only (for MCP where Copilot filters).

    Returns a summary dict with stats.
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
    store = VectorStore()
    store.create_collection(collection_name)
    store.clear_collection()
    store.add_chunks(embedded)
    _log(f"[full_index] Stored {len(embedded)} chunks in ChromaDB")

    return {
        "repo_path": repo_path,
        "tech_stack": tech_stack,
        "files_scanned": len(files),
        "chunks_created": len(chunks),
        "chunks_embedded": len(embedded),
    }

def full_index_parallel(repo_path: str = "", collection_name: str = "codebase",
                        use_llm_filter: bool = True) -> dict:
    """Full pipeline with parallel chunking + embedding.

    Same as full_index() but overlaps chunking (CPU) with embedding (GPU)
    using a producer-consumer pattern with threads.
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
    store = VectorStore()
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

def index_from_paths_parallel(paths: list[str], repo_path: str = "",
                              collection_name: str = "codebase") -> dict:
    """Parallel version of index_from_paths.

    Same file-matching logic, but uses producer-consumer for chunk+embed.
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
    store = VectorStore()
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



def index_from_paths(paths: list[str], repo_path: str = "",
                     collection_name: str = "codebase") -> dict:
    """Index files matching the given paths or directories.

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
    for f in all_files:
        fp = f["file_path"]
        if fp in path_set:
            files.append(f)
            continue
        for p in path_set:
            if fp.startswith(p + "/") or fp.startswith(p.rstrip("/") + "/"):
                files.append(f)
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
    store = VectorStore()
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


def reindex(repo_path: str = "", collection_name: str = "codebase") -> dict:
    """Smart re-index: only re-embed files that changed, add new, remove deleted.

    Returns a summary dict with stats.
    """
    repo_path = repo_path or settings.repo_path
    _log(f"[reindex] Smart re-indexing: {repo_path}")

    # Step 1: Scan directory → current files
    files = scan_directory(repo_path)
    current_files = {file["file_path"]: file for file in files}
    _log(f"[reindex] Scanned: {len(files)} files")

    # Step 2: Get already-indexed files from ChromaDB
    store = VectorStore()
    store.create_collection(collection_name)
    indexed_files = store.get_indexed_files()
    _log(f"[reindex] Indexed files in DB: {len(indexed_files)}")

    # Step 3: Chunk current files to get hashes
    chunks_by_file = {}
    for file in files:
        file_chunk = chunk_file(file)
        if file_chunk:
            chunks_by_file[file["file_path"]] = file_chunk

    # Step 4: Compare — find new, changed, deleted, unchanged
    current_paths = set(current_files.keys())
    indexed_paths = set(indexed_files.keys())

    new_files = current_paths - indexed_paths
    deleted_files = indexed_paths - current_paths
    maybe_changed_files = current_paths & indexed_paths

    changed_files = set()
    unchanged_files = set()

    for file_path in maybe_changed_files:
        file_chunks = chunks_by_file.get(file_path, [])
        new_hash = file_chunks[0]["file_hash"] if file_chunks else ""
        old_hash = indexed_files.get(file_path, "")

        if new_hash != old_hash:
            changed_files.add(file_path)
        else:
            unchanged_files.add(file_path)

    _log(f"[reindex] New: {len(new_files)}, Changed: {len(changed_files)}, "
          f"Deleted: {len(deleted_files)}, Unchanged: {len(unchanged_files)}")
    
    # Step 5: Delete chunks for removed & changed files
    for file_path in deleted_files | changed_files:
        store.delete_by_file(file_path)

    # Step 6: Embed & add chunks for new & changed files
    to_embed = []
    
    for file_path in new_files | changed_files:
        to_embed.extend(chunks_by_file.get(file_path, []))

    if to_embed:
        embedded = embed_chunks(to_embed)
        store.add_chunks(embedded)
        _log(f"[reindex] Embedded & stored {len(embedded)} new/changed chunks")
    else :
        _log("[reindex] Nothing to embed — all files unchanged")

    return {
        "repo_path": repo_path,
        "new_files": len(new_files),
        "changed_files": len(changed_files),
        "deleted_files": len(deleted_files),
        "unchanged_files": len(unchanged_files),
        "chunks_embedded": len(to_embed),
    }






