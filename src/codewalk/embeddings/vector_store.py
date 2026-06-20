import logging

import chromadb
from src.codewalk.embeddings.embedder import get_embedding_model
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

_CHROMA_BATCH = 500

class VectorStore:
    def __init__(self, persist_dir: str = "./data/chroma"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_model = get_embedding_model()
        self.parents_collection = None
        self.children_collection = None
        self.collection = None
        self._collection_prefix = "codebase"

    def create_collection(self, name: str = "codebase"):
        """Create parent + child ChromaDB collections, prefixed with repo name."""
        self._collection_prefix = name
        self.parents_collection = self.client.get_or_create_collection(
            name=f"{name}_parents",
            metadata={"hnsw:space": "cosine"}
        )
        self.children_collection = self.client.get_or_create_collection(
            name=f"{name}_children",
            metadata={"hnsw:space": "cosine"}
        )
        self.collection = self.parents_collection
        return self.collection

    def chunk_count(self) -> int:
        """Total number of indexed chunks."""
        return self.parents_collection.count() if self.parents_collection else 0

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """Search via parent-child retrieval."""
        return self.search_with_parents(query, n_results=n_results)
    
    def get_file_hash(self, file_path: str) -> str | None:
        """Get the stored content hash for a file. Returns None if not indexed."""
        results = self.parents_collection.get(
            where={"file_path": file_path},
            limit=1,
            include=["metadatas"],
        )

        if results["metadatas"]:
            return results["metadatas"][0].get("file_hash")
        return None
    
    def get_all_indexed_files(self) -> set[str]:
        """Get all unique file paths currently in the index.

        Batched in 5,000-chunk increments to avoid OOM on large codebases.
        """
        BATCH_SIZE = 5000
        offset = 0
        file_paths = set()
        while True:
            batch = self.parents_collection.get(
                include=["metadatas"],
                limit=BATCH_SIZE,
                offset=offset,
            )
            if not batch["ids"]:
                break
            for meta in batch["metadatas"]:
                if "file_path" in meta:
                    file_paths.add(meta["file_path"])
            offset += BATCH_SIZE
        return file_paths

    def get_all_chunks(self) -> list[dict]:
        """Return all parent chunk metadata from ChromaDB.

        Used to fully rebuild DuckDB + knowledge graph after an incremental
        reindex, so the graph store contains every chunk (not just changed ones).
        """
        BATCH_SIZE = 5000
        offset = 0
        chunks: list[dict] = []
        while True:
            batch = self.parents_collection.get(
                include=["metadatas"],
                limit=BATCH_SIZE,
                offset=offset,
            )
            if not batch["ids"]:
                break
            chunks.extend(batch["metadatas"])
            offset += BATCH_SIZE
        return chunks
    
    def delete_by_file(self, file_path: str):
        """Delete ALL chunks for a specific file from all collections."""
        self.parents_collection.delete(where={"file_path": file_path})
        self.children_collection.delete(where={"file_path": file_path})

    def get_symbols_by_files(self, file_paths: list[str]) -> dict[str, list[dict]]:
        """Get all symbols (function/class chunks) grouped by file path.

        Returns: {"file/path.py": [{"symbol_name": "foo", "symbol_type": "function", ...}, ...]}
        """
        if not file_paths:
            return {}

        where_filter = (
            {"file_path": file_paths[0]}
            if len(file_paths) == 1
            else {"$or": [{"file_path": fp} for fp in file_paths]}
        )
        results = self.parents_collection.get(where=where_filter, include=["metadatas"])

        from collections import defaultdict
        by_file: dict[str, list[dict]] = defaultdict(list)
        for meta in results["metadatas"]:
            if meta.get("symbol_name"):
                by_file[meta["file_path"]].append({
                    "symbol_name": meta["symbol_name"],
                    "symbol_type": meta["symbol_type"],
                    "start_line": meta["start_line"],
                    "end_line": meta["end_line"],
                })
        # Sort each file's symbols by start_line
        for fp in by_file:
            by_file[fp].sort(key=lambda s: s["start_line"])
        return dict(by_file)

    def get_indexed_files(self) -> dict[str, str]:
        """Get all file paths currently in ChromaDB with their content hashes.

        Returns: {"file/path.py": "abc123hash", ...}
        """
        result = self.parents_collection.get(include=["metadatas"])

        files = {}

        for metadata in result["metadatas"]:
            file_path = metadata.get("file_path")
            file_hash = metadata.get("file_hash", "")

            if file_path not in files:
                files[file_path] = file_hash

        return files

    def add_parent_child_chunks(self, chunks: list[dict]):
        """Store chunks in their respective parent/child collections.

        Routing rules:
          chunk_type="parent" with children  → parents collection ONLY
          chunk_type="parent" without children → BOTH (it's small, searchable)
          chunk_type="child"    → children collection ONLY
          chunk_type="leftover" → BOTH (they are their own context)
        """
        parent_ids_with_children = {
            chunk["parent_chunk_id"]
            for chunk in chunks
            if chunk.get("chunk_type") == "child" and chunk.get("parent_chunk_id")
        }

        parents_batch = []
        children_batch = []

        for chunk in chunks:
            chunk_type = chunk.get("chunk_type", "leftover")

            if chunk_type == "parent":
                parents_batch.append(chunk)

                parent_id = f"{chunk['file_path']}::parent::{chunk['chunk_index']}"
                if parent_id not in parent_ids_with_children:
                    children_batch.append(chunk)

            elif chunk_type == "child":
                children_batch.append(chunk)
            
            elif chunk_type == "leftover":
                # Leftovers go in both — they ARE their own context
                parents_batch.append(chunk)
                children_batch.append(chunk)

        # Store in respective collections
        if parents_batch:
            self._store_in_collection(self.parents_collection, parents_batch)
        if children_batch:
            self._store_in_collection(self.children_collection, children_batch)

    def _store_in_collection(self, collection, chunks: list[dict]):
        """Store a batch of chunks into a specific ChromaDB collection.

        Same batched upsert logic as add_chunks(), but takes a
        collection parameter. Uses file_path::chunk_type::chunk_index as ID.
        """
        total = len(chunks)

        for start in range(0, total, _CHROMA_BATCH):
            end = min(start + _CHROMA_BATCH, total)
            batch = chunks[start:end]
            collection.upsert(
                ids=[
                    f"{chunk['file_path']}::{chunk.get('chunk_type', 'leftover')}::{chunk['chunk_index']}"
                    for chunk in batch
                ],
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
                        "chunk_type": chunk.get("chunk_type", "leftover"),
                        "parent_chunk_id": chunk.get("parent_chunk_id") or "",
                    }

                    for chunk in batch
                ]
            )

    def search_with_parents(self, query: str, n_results: int = 5) -> list[dict]:
        """Search children collection → batch-fetch parents → return full context."""
        query_vector = self.embedding_model.embed_query(query)

        # Step 1: search children for precise matches
        child_results = self.children_collection.query(
            query_embeddings=[query_vector],
            n_results=n_results,
        )

        # Step 2: collect unique parent IDs + best distance for each parent
        parent_ids_to_fetch = {} 
        standalone_results = []
        seen_standalone = set()

        for index in range(len(child_results["ids"][0])):
            child_meta = child_results["metadatas"][0][index]
            child_text = child_results["documents"][0][index]
            child_distance = (
                child_results["distances"][0][index]
                if child_results.get("distances")
                else None
            )
            parent_id = child_meta.get("parent_chunk_id", "")

            if parent_id:
                if parent_id not in parent_ids_to_fetch or \
                (child_distance is not None and child_distance < parent_ids_to_fetch[parent_id]):
                    parent_ids_to_fetch[parent_id] = child_distance
            else:
                # Leftover or parent-only chunk — no parent to look up
                chunk_id = child_results["ids"][0][index]
                if chunk_id not in seen_standalone:
                    seen_standalone.add(chunk_id)
                    standalone_results.append({
                        "id": chunk_id,
                        "text": child_text,
                        "metadata": child_meta,
                        "distance": child_distance,
                    })

        # Step 3: ONE batch fetch for all parents
        formatted = []

        if parent_ids_to_fetch:
            parent_id_list = list(parent_ids_to_fetch.keys())
            parent_result = self.parents_collection.get(
                ids=parent_id_list,
                include=["documents", "metadatas"]
            )
            for index, parent_id in enumerate(parent_result["ids"]):
                formatted.append({
                    "id": parent_id,
                    "text": parent_result["documents"][index],
                    "metadata": parent_result["metadatas"][index],
                    "distance": parent_ids_to_fetch.get(parent_id),
                })

        formatted.extend(standalone_results)

        return formatted

    def clear_collection(self):
        """Delete all collections and recreate them."""
        for col in self.client.list_collections():
            col_name = col if isinstance(col, str) else col.name
            if col_name in (self.parents_collection.name, self.children_collection.name):
                self.client.delete_collection(col_name)
        self.create_collection(self._collection_prefix)


    