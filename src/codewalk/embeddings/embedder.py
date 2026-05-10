import math

from langchain_core.embeddings import Embeddings
from src.codewalk.config import settings

def get_embedding_model() -> Embeddings:
    """Factory: returns the right embedding model based on settings.llm_provider."""
    provider = settings.llm_provider.lower()

    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(model=settings.embedding_model)

    elif provider == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
        )

    elif provider == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        return GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
            google_api_key=settings.google_api_key,
        )

    elif provider in ("groq", "anthropic", "openrouter", "github_models"):
        # No embeddings API — fallback to Ollama
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(model=settings.embedding_model)

    else:
        raise ValueError(
            f"Unknown provider: '{provider}'. "
            f"Supported: ollama, openai, gemini, groq, anthropic, openrouter, github_models"
        )

def _sanitize_text(text: str) -> str:
    """Remove NUL bytes and control characters that break Ollama."""
    # Remove NUL bytes
    text = text.replace("\x00", "")
    # Remove other control chars (keep newline, tab, carriage return)
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t", "\r") or (ord(ch) >= 32)
    )
    return text

def _has_nan(vector: list[float]) -> bool:
    """Check if any value in the embedding vector is NaN."""
    return any(math.isnan(v) for v in vector)

def embed_chunks(chunks: list[dict]) -> list[dict]:
    """Add embedding vectors to each chunk."""
    model = get_embedding_model()
    embedded = []

    for chunk in chunks:
        text = chunk["text"]

        # Skip empty/whitespace-only chunks
        if not text or not text.strip():
            continue

        # Sanitize text before sending to Ollama
        clean_text = _sanitize_text(text)
        if not clean_text.strip():
            continue

        try:
            vector = model.embed_query(clean_text)

            # Skip if Ollama returned NaN values
            if _has_nan(vector):
                print(f"Skipping chunk {chunk['file_path']}::chunk{chunk['chunk_index']}: NaN in embedding")
                continue

            chunk["embedding"] = vector
            embedded.append(chunk)
        except Exception as e:
            print(f"Error embedding chunk {chunk['file_path']}::chunk{chunk['chunk_index']}: {e}")

    return embedded