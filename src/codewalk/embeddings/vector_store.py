import logging
import sys

import chromadb
from src.codewalk.embeddings.embedder import get_embedding_model

logger = logging.getLogger("codewalk")

def _log(msg: str):
    print(msg, file=sys.stderr)
    logger.info(msg)

class VectorStore:
    def __init__(self, persist_dir: str = "./data/chroma"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_model = get_embedding_model()

    def create_collection(self, name: str = "codebase"):
        """Create or get a ChromaDB collection."""
        self.collection = self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"}
        )

        return self.collection

    def add_chunks(self, chunks: list[dict]):
        """Store embedded chunks in ChromaDB (batched to avoid limits)."""
        CHROMA_BATCH = 500
        total = len(chunks)

        for start in range(0, total, CHROMA_BATCH):
            end = min(start + CHROMA_BATCH, total)
            batch = chunks[start:end]
            self.collection.add(
                ids=[f"{chunk['file_path']}::chunk{chunk['chunk_index']}" for chunk in batch],
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
        """Search the vector store with a natural language query."""
        query_vector = self.embedding_model.embed_query(query)
        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=n_results,
        )

        formatted = []

        for index in range(len(results["ids"][0])):
            formatted.append({
                "id": results["ids"][0][index],
                "text": results["documents"][0][index],
                "metadata": results["metadatas"][0][index],
                "distance": results["distances"][0][index] if results.get("distances") else None
            })
        
        return formatted
    
    def delete_by_file(self, file_path: str):
        """Delete ALL chunks for a specific file from ChromaDB."""
        self.collection.delete(where={"file_path": file_path})

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
        results = self.collection.get(where=where_filter, include=["metadatas"])

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
        result = self.collection.get(include=["metadatas"])

        files = {}

        for metadata in result["metadatas"]:
            file_path = metadata.get("file_path")
            file_hash = metadata.get("file_hash", "")

            if file_path not in files:
                files[file_path] = file_hash

        return files
    
    def clear_collection(self):
        """Delete the entire collection and recreate it."""
        name = self.collection.name
        self.client.delete_collection(name)
        self.create_collection(name)