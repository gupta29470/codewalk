from src.codewalk.ingestion.scanner import scan_directory
from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.embeddings.chunker import chunk_all_files, chunk_file
from src.codewalk.embeddings.embedder import embed_chunks
from src.codewalk.embeddings.vector_store import VectorStore

def full_index(repo_path: str, collection_name: str = "codebase") -> dict:
    """Full pipeline: scan → chunk → embed → store. Nukes old data first.

    Returns a summary dict with stats.
    """
    print(f"Full indexing: {repo_path}")
    
    # Step 1: Detect tech stack (just for info)
    tech_stack = detect_tech_stack(repo_path)
    print(f"Tech stack: {tech_stack}")

    # Step 2: Scan directory
    files = scan_directory(repo_path)
    print(f"Scanned {len(files)} files.")

    # Step 3: Chunk all files
    chunks = chunk_all_files(files)
    print(f"Generated {len(chunks)} chunks.")

    # Step 4: Embed all chunks
    embedded = embed_chunks(chunks)
    print(f"Embedded {len(embedded)} chunks.")

    # Step 5: Store in ChromaDB (nuke old data first)
    store = VectorStore()
    store.create_collection(collection_name)
    store.clear_collection()
    store.add_chunks(embedded)
    print(f"Stored {len(embedded)} chunks in ChromaDB")

    return {
        "repo_path": repo_path,
        "tech_stack": tech_stack,
        "files_scanned": len(files),
        "chunks_created": len(chunks),
        "chunks_embedded": len(embedded),
    }

def reindex(repo_path: str, collection_name: str = "codebase") -> dict:
    """Smart re-index: only re-embed files that changed, add new, remove deleted.

    Returns a summary dict with stats.
    """
    print(f"Smart re-indexing: {repo_path}")

    # Step 1: Scan directory → current files
    files = scan_directory(repo_path)
    current_files = {file["file_path"]: file for file in files}
    print(f"Scanned: {len(files)} files")

    # Step 2: Get already-indexed files from ChromaDB
    store = VectorStore()
    store.create_collection(collection_name)
    indexed_files = store.get_indexed_files()
    print(f"Indexed files in DB: {len(indexed_files)}")

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

    print(f"New: {len(new_files)}, Changed: {len(changed_files)}, "
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
        print(f"Embedded & stored {len(embedded)} new/changed chunks")
    else :
        print("Nothing to embed — all files unchanged")

    return {
        "repo_path": repo_path,
        "new_files": len(new_files),
        "changed_files": len(changed_files),
        "deleted_files": len(deleted_files),
        "unchanged_files": len(unchanged_files),
        "chunks_embedded": len(to_embed),
    }






