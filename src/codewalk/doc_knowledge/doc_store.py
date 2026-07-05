"""ChromaDB-backed doc/guideline store with section-aware chunking."""
import logging

import chromadb

from src.codewalk.embeddings.embedder import (
    _clear_gpu_cache,
    _MAX_CHUNK_CHARS,
    get_embedding_model,
)
from src.codewalk.doc_knowledge.doc_parser import parse_all_docs
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

_CHROMA_BATCH = 500
_DOC_EMBED_BATCH = 16
_DOC_MPS_BATCH = 4
_DOC_FALLBACK_BATCHES = (4, 1)


def _doc_embed_batch_size(embedding_model) -> int:
    """Use a smaller batch on MPS to avoid the 24GB allocation crash."""
    device = getattr(embedding_model, "_device", "")
    if device == "mps":
        return _DOC_MPS_BATCH
    return _DOC_EMBED_BATCH


class DocStore:
    """ChromaDB-backed store for document chunks.

    TEACH: Lifecycle:
      1. __init__(persist_dir) → connects to ChromaDB on disk
      2. create_collection()   → creates/gets the "docs" collection
      3. index_docs(path)      → parse + embed + store all docs in a folder
      4. search(query, n)      → semantic search across doc chunks
      5. delete_doc(doc_path)  → remove all chunks for one document
      6. clear()               → wipe the entire docs collection
    """
    def __init__(self, persist_dir: str = "./data/chroma", collection_name: str = "docs"):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_model = get_embedding_model()
        self.collection = None
        self._collection_name = collection_name

    def create_collection(self):
        """Create or get the 'docs' collection.

        TEACH: get_or_create_collection is idempotent — safe to call
               multiple times. cosine space matches what we use for code.
        """
        self.collection = self.client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )     

        return self.collection
    
    def chunk_count(self) -> int:
         """Total number of indexed doc chunks."""
         return self.collection.count() if self.collection else 0
    
    def index_docs(self, docs_path) -> dict:
        """Parse, embed, and store all documents in a directory.

        TEACH: This is the main entry point — equivalent to pipeline.py's
               index flow but for documents. Steps:
                 1. parse_all_docs() → list of chunks with text + metadata
                 2. Embed chunk texts in batches
                 3. Upsert into ChromaDB with stable IDs

        Args:
            docs_path: Absolute path to the docs folder.

        Returns:
            {"docs_found": int, "chunks_stored": int}
        """
        if not self.collection:
            self.create_collection()

        # Step 1: Parse
        chunks = parse_all_docs(docs_path)
        if not chunks:
            _log("[doc_store] No document chunks found.")
            return {"docs_found": 0, "chunks_stored": 0}

        # Cap long chunks and sort by length so similar-length texts batch
        # together. This reduces padding waste and lowers peak GPU memory.
        for chunk in chunks:
            chunk["text"] = chunk["text"][:_MAX_CHUNK_CHARS]
        chunks.sort(key=lambda c: len(c["text"]))

        total = len(chunks)
        batch_size = _doc_embed_batch_size(self.embedding_model)

        # Step 2: Embed texts in batches with MPS-safe fallback.
        _log(f"[doc_store] Embedding {total} doc chunks in batches of {batch_size}...")
        embeddings: list[list[float] | None] = [None] * total
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_chunks = chunks[start:end]
            _log(f"[doc_store] Embedding doc chunks {start}-{end}/{total}...")
            batch_embeddings = self._embed_batch(batch_chunks)
            for offset, vector in enumerate(batch_embeddings):
                embeddings[start + offset] = vector

        # Step 3: Upsert in batches
        for start in range(0, total, _CHROMA_BATCH):
            end = min(start + _CHROMA_BATCH, total)
            batch_chunks = chunks[start:end]
            batch_embeddings = embeddings[start:end]

            self.collection.upsert(
                ids=[
                    f"{chunk['metadata']['doc_path']}::{chunk['metadata']['chunk_index']}"
                    for chunk in batch_chunks
                ],
                embeddings=batch_embeddings,
                documents=[chunk["text"] for chunk in batch_chunks],
                metadatas=[chunk["metadata"] for chunk in batch_chunks],
            )

        unique_docs = {chunk["metadata"]["doc_path"] for chunk in chunks}

        _log(f"[doc_store] Indexed {len(unique_docs)} docs → {total} chunks")
        return {"docs_found": len(unique_docs), "chunks_stored": total}

    def _embed_batch(self, batch_chunks: list[dict]) -> list[list[float]]:
        """Embed one batch of chunks, falling back to smaller batches on errors."""
        batch_texts = [chunk["text"] for chunk in batch_chunks]

        try:
            return self.embedding_model.embed_documents(batch_texts)
        except Exception as e:
            _log(f"[doc_store] Batch embed failed ({len(batch_chunks)} chunks): {e}")

        for mini_size in _DOC_FALLBACK_BATCHES:
            try:
                _log(f"[doc_store] Retrying in mini-batches of {mini_size}...")
                results: list[list[float]] = []
                for mini_start in range(0, len(batch_chunks), mini_size):
                    mini_end = mini_start + mini_size
                    mini_texts = batch_texts[mini_start:mini_end]
                    mini_vectors = self.embedding_model.embed_documents(mini_texts)
                    results.extend(mini_vectors)
                return results
            except Exception as e2:
                _log(f"[doc_store] Mini-batch size {mini_size} failed: {e2}")
                _clear_gpu_cache()

        # Last resort: embed one chunk at a time.
        _log("[doc_store] Falling back to single-chunk embedding...")
        results = []
        for chunk in batch_chunks:
            try:
                vector = self.embedding_model.embed_query(chunk["text"])
                results.append(vector)
            except Exception as e3:
                _clear_gpu_cache()
                _log(f"[doc_store] SKIP {chunk['metadata'].get('doc_path')}::chunk{chunk['metadata'].get('chunk_index')}: {e3}")
                dim = getattr(self.embedding_model, "_model", None)
                dim = dim.get_embedding_dimension() if dim else 1536
                results.append([0.0] * dim)
        return results
    
    def search(self, query: str, n_results: int = 5) -> list[dict]:
        """Semantic search across document chunks.

        TEACH: Same pattern as VectorStore.search() —
               embed the query, find nearest neighbors in cosine space.

        Returns:
            [{"text": "...", "metadata": {...}, "distance": 0.23}, ...]
        """
        if not self.collection or self.collection.count() == 0:
            return []

        query_vector = self.embedding_model.embed_query(query)

        results = self.collection.query(
            query_embeddings=[query_vector],
            n_results=n_results
        )

        return self._format_results(results)

    def multi_search(self, queries: list[str], n_results: int = 5) -> list[dict]:
        """Run several search queries and return deduplicated, merged results.

        Useful for broad doc questions where a single phrasing might miss
        relevant chunks. Results are ordered by first appearance across queries.

        Args:
            queries: List of search phrasings for the same underlying question.
            n_results: Number of results to fetch per query.

        Returns:
            [{"text": "...", "metadata": {...}, "distance": 0.23}, ...]
        """
        if not self.collection or self.collection.count() == 0:
            return []

        seen = set()
        merged: list[dict] = []

        for query in queries:
            query_vector = self.embedding_model.embed_query(query)
            results = self.collection.query(
                query_embeddings=[query_vector],
                n_results=n_results,
            )
            for item in self._format_results(results):
                meta = item["metadata"]
                key = (meta.get("doc_path"), meta.get("chunk_index"))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)

        return merged

    def _format_results(self, results: dict) -> list[dict]:
        """Convert a ChromaDB query result into a list of result dicts."""
        formatted = []
        for index in range(len(results["ids"][0])):
            formatted.append({
                "text": results["documents"][0][index],
                "metadata": results["metadatas"][0][index],
                "distance": (
                    results["distances"][0][index]
                    if results.get("distances")
                    else None
                ),
            })
        return formatted
    
    def delete_doc(self, doc_path: str):
        """Delete all chunks for a specific document.

        TEACH: Uses ChromaDB's where filter — same as
               VectorStore.delete_by_file() but filters on doc_path.
        """
        if self.collection:
            self.collection.delete(where={"doc_path": doc_path})

    def get_all_indexed_docs(self) -> set[str]:
        """Get all unique doc_path values currently indexed.

        TEACH: Useful for checking what's already indexed before re-indexing.
        """
        if not self.collection or self.collection.count() == 0:
            return set()
        
        results = self.collection.get(include=["metadatas"])
        return {
            meta["doc_path"]
            for meta in results["metadatas"]
            if "doc_path" in meta
        }
    
    def clear(self):
        """Delete the entire docs collection and recreate it.

        TEACH: Nuclear option — wipes all indexed docs.
               Used when user wants to re-index from scratch.
        """
        self.client.delete_collection(self._collection_name)
        self.create_collection()

        

        

    
