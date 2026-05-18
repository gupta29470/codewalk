"""
=============================================================================
 vector_store.py — ChromaDB Vector Database Wrapper
=============================================================================

WHAT THIS FILE DOES:
    Stores and retrieves code embeddings using ChromaDB (a vector database).
    Think of it as a specialized database where you search by MEANING
    instead of exact text matches.

HOW IT WORKS:
    1. Stores chunks as: {id, embedding_vector, text, metadata}
    2. On search: converts query to vector → finds nearest vectors in the DB
    3. Returns the code chunks whose meaning is closest to your query

REAL-WORLD ANALOGY:
    Regular database: "Find all rows where name = 'login'"  (exact match)
    Vector database:  "Find code that MEANS something like 'authentication'"
                      (semantic match — finds login(), authenticate(), verify_token())

WHY ChromaDB?
    - Runs locally (no cloud dependency)
    - Persists to disk (survives restarts)
    - Fast cosine similarity search on vectors
    - Supports metadata filtering (search within specific files/languages)

KEY CONCEPTS:
    - Collection: like a database table. We use one per project.
    - HNSW: the search algorithm ChromaDB uses internally (fast approximate
      nearest neighbor search). We configure it with cosine distance.
    - Upsert: "update or insert" — if a chunk already exists, overwrite it.
      This is how incremental reindex works without duplicating chunks.

WHO CALLS THIS:
    - pipeline.py: index_from_paths_parallel() → stores embedded chunks
    - server.py: codewalk_search_codebase() → searches for code by meaning
    - server.py: codewalk_explain_function() → finds function source code

DEPENDENCIES:
    - chromadb: The vector database engine
    - embedder.py: provides the embedding model for search queries

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging

import chromadb  # Vector database — stores and searches embeddings
from src.codewalk.embeddings.embedder import get_embedding_model
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


# =============================================================================
# VectorStore — The Main Class
# =============================================================================

class VectorStore:
    """Wrapper around ChromaDB for storing and searching code embeddings.

    LIFECYCLE:
        1. __init__(persist_dir) → connects to ChromaDB on disk
        2. create_collection("codebase") → creates/gets the collection
        3. add_chunks([...]) → stores embedded code chunks
        4. search("query") → finds similar code
        5. (optional) delete_by_file() → remove stale chunks during reindex
    """

    def __init__(self, persist_dir: str = "./data/chroma"):
        """Connect to ChromaDB.

        persist_dir is where ChromaDB stores its data on disk.
        Default: ./data/chroma (relative to project root)
        PersistentClient means data survives when the process exits.
        """
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_model = get_embedding_model()

    def create_collection(self, name: str = "codebase"):
        """Create (or get existing) ChromaDB collection.

        A collection is like a database table — it holds all embeddings
        for one project. The name is used as an identifier.

        hnsw:space = "cosine": tells ChromaDB to measure similarity using
        cosine distance. This works well with normalized embeddings from Jina.
        Cosine similarity: 1.0 = identical, 0.0 = unrelated.
        """
        self.collection = self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"}
        )
        return self.collection

    def add_chunks(self, chunks: list[dict]):
        """Store embedded chunks in ChromaDB (batched to stay under limits).

        EXECUTION FLOW:
            1. Receives chunks with embeddings: [{"text": "...", "embedding": [...], ...}]
            2. Splits into batches of 500 (ChromaDB has per-request size limits)
            3. For each batch, calls collection.upsert() with:
               - ids: unique chunk identifier (file_path::chunk0, file_path::chunk1, ...)
               - embeddings: the vector (list of 768 floats)
               - documents: the raw text (for retrieval — so we can show it back)
               - metadatas: structured info (file, language, symbol name, lines)
            4. upsert = "if this id exists, overwrite; otherwise insert"

        WHY UPSERT (not insert)?
            During incremental reindex, a file might have changed.
            The chunk ids stay the same (file_path::chunk0) but content differs.
            Upsert overwrites the old embedding with the new one.
            Insert would crash with "duplicate id" errors.

        WHY BATCH 500?
            ChromaDB has memory limits per request. 500 chunks with metadata
            fits comfortably. Much larger and you risk out-of-memory errors.
        """
        CHROMA_BATCH = 500
        total = len(chunks)

        for start in range(0, total, CHROMA_BATCH):
            end = min(start + CHROMA_BATCH, total)
            batch = chunks[start:end]
            self.collection.upsert(
                ids=[f"{chunk[\'file_path\']}::chunk{chunk[\'chunk_index\']}" for chunk in batch],
                embeddings=[chunk["embedding"] for chunk in batch],
                documents=[chunk["text"] for chunk in batch],
                metadatas=[
                    {
                        "file_path": chunk["file_path"],
                        "language": chunk["language"],
                        "chunk_index": chunk["chunk_index"],
                        "source": chunk.get("source", "text_splitter"),
                        "symbol_name": chunk.get("symbol_name") or "",
                        "symbol_type": chunk.get("symbol_type") or "",
                        "start_line": chunk.get("start_line") or 0,
                        "end_line": chunk.get("end_line") or 0,
                        "file_hash": chunk.get("file_hash") or "",
                    }
                    for chunk in batch
                ]
            )
            _log(f"  Stored {end}/{total} chunks in ChromaDB")

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """Search the vector store with a natural language query.

        EXECUTION FLOW:
            1. query = "authentication logic"
            2. embed_query("authentication logic") → [0.12, -0.34, ...] (768 numbers)
            3. ChromaDB finds the 5 nearest vectors in its index (HNSW algorithm)
            4. Returns those chunks with their text, metadata, and distance score

        DISTANCE INTERPRETATION (cosine):
            0.0 = identical meaning
            0.5 = somewhat related
            1.0 = completely unrelated
            Lower = better match

        Args:
            query: Natural language, e.g. "how does file scanning work"
            n_results: How many results to return (default 5)

        Returns:
            List of dicts: [{"id": "...", "text": "...", "metadata": {...}, "distance": 0.23}]
        """
        # Convert query text → vector using the SAME model used during indexing
        query_vector = self.embedding_model.embed_query(query)
        # Find nearest neighbors in ChromaDB
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=n_results,
        )

        # Reformat ChromaDB's grouped response into per-result dicts
        formatted = []
        for index in range(len(results["ids"][0])):
            formatted.append({
                "id": results["ids"][0][index],
                "text": results["documents"][0][index],
                "metadata": results["metadatas"][0][index],
                "distance": results["distances"][0][index] if results.get("distances") else None
            })
        return formatted
    
    def get_file_hash(self, file_path: str) -> str | None:
        """Get the stored content hash for a file (used for change detection).

        During incremental reindex, we compare:
          stored_hash (from ChromaDB) vs current_hash (from reading the file)
        If they differ → file changed → re-embed it.
        If they match → file unchanged → skip it (save time).
        """
        results = self.collection.get(
            where={"file_path": file_path},
            limit=1,
            include=["metadatas"],
        )
        if results["metadatas"]:
            return results["metadatas"][0].get("file_hash")
        return None
    
    def get_all_indexed_files(self) -> set[str]:
        """Get all unique file paths currently in the index.

        Used by incremental reindex to detect DELETED files:
        if a file is in ChromaDB but no longer on disk → delete its chunks.
        """
        results = self.collection.get(include=["metadatas"])
        return {
            meta["file_path"]
            for meta in results["metadatas"]
            if "file_path" in meta
        }
    
    def delete_by_file(self, file_path: str):
        """Delete ALL chunks for a specific file.

        Called during incremental reindex when:
          - A file was deleted from disk → remove its stale embeddings
          - A file changed → delete old chunks, then re-embed fresh ones
        """
        self.collection.delete(where={"file_path": file_path})

    def get_symbols_by_files(self, file_paths: list[str]) -> dict[str, list[dict]]:
        """Get all function/class symbols grouped by file path.

        Used by codewalk_get_module_info() to show what symbols each file contains.

        Returns: {"auth/service.py": [
            {"symbol_name": "login", "symbol_type": "function", "start_line": 10, "end_line": 25},
            {"symbol_name": "AuthService", "symbol_type": "class", "start_line": 1, "end_line": 50},
        ]}
        """
        if not file_paths:
            return {}

        # ChromaDB where filter: either single match or $or for multiple
        where_filter = (
            {"file_path": file_paths[0]}
            if len(file_paths) == 1
            else {"$or": [{"file_path": fp} for fp in file_paths]}
        )
        results = self.collection.get(where=where_filter, include=["metadatas"])

        from collections import defaultdict
        by_file: dict[str, list[dict]] = defaultdict(list)
        for meta in results["metadatas"]:
            if meta.get("symbol_name"):  # Only include named symbols (skip text chunks)
                by_file[meta["file_path"]].append({
                    "symbol_name": meta["symbol_name"],
                    "symbol_type": meta["symbol_type"],
                    "start_line": meta["start_line"],
                    "end_line": meta["end_line"],
                })
        # Sort symbols by where they appear in the file
        for fp in by_file:
            by_file[fp].sort(key=lambda s: s["start_line"])
        return dict(by_file)

    def get_indexed_files(self) -> dict[str, str]:
        """Get all file paths with their content hashes.

        Returns: {"file/path.py": "abc123hash", ...}
        Used during incremental reindex to check what's already indexed.
        """
        result = self.collection.get(include=["metadatas"])
        files = {}
        for metadata in result["metadatas"]:
            file_path = metadata.get("file_path")
            file_hash = metadata.get("file_hash", "")
            if file_path not in files:
                files[file_path] = file_hash
        return files
    
    def clear_collection(self):
        """Nuclear option: delete everything and start fresh.

        Used when: the embedding model changed (vectors are incompatible),
        or the collection is corrupted.
        """
        name = self.collection.name
        self.client.delete_collection(name)
        self.create_collection(name)
