from langchain_ollama import OllamaEmbeddings
from src.codewalk.config import settings

def get_embedding_model() -> OllamaEmbeddings:
    """Create an embedding model using Ollama."""
    return OllamaEmbeddings(model=settings.embedding_model)

def embed_chunks(chunks: list[dict]) -> list[dict]:
    """Add embedding vectors to each chunk."""
    model = get_embedding_model()
    embedded = []

    for chunk in chunks:
        try:
            vector = model.embed_query(chunk["text"])
            chunk["embedding"] = vector
            embedded.append(chunk)
        except Exception as e:
            print(f"Error embedding chunk {chunk['file_path']}::chunk{chunk['chunk_index']}: {e}")

    return embedded