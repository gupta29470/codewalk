import chromadb
from src.codewalk.embeddings.embedder import get_embedding_model

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
        """Store embedded chunks in ChromaDB."""
        self.collection.add(
            ids=[f"{chunk['file_path']}::chunk{chunk['chunk_index']}" for chunk in chunks],
            embeddings=[chunk["embedding"] for chunk in chunks],
            documents=[chunk["text"] for chunk in chunks],
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
                }
                for chunk in chunks
            ]
        )

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