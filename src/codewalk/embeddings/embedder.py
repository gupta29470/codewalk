import torch
from sentence_transformers import SentenceTransformer
from langchain_core.embeddings import Embeddings
from src.codewalk.config import settings
from src.codewalk.log import log as _log

def _detect_device() -> str:
    """Auto-detect the best available compute device."""
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"

class JinaCodeEmbeddings(Embeddings):
    """LangChain-compatible wrapper around Jina code embedding model.

    Implements embed_query() and embed_documents() so VectorStore
    and ChromaDB work without any changes.
    """

    def __init__(self, model_name: str = "", device: str = ""):
        model_name = model_name or settings.embedding_model
        device = device or _detect_device()
        _log(f"Loading embedding model: {model_name} (device={device})")
        self._model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
        )
        _log(f"Embedding model loaded: {model_name}")
    
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        vectors = self._model.encode(text, normalize_embeddings=True)
        return vectors.tolist()
    
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of documents (batch)."""
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vectors.tolist()
    
# Singleton — load model once, reuse everywhere
_embedding_model: JinaCodeEmbeddings | None = None

def get_embedding_model() -> Embeddings:
    """Returns the Jina code embedding model (singleton)."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = JinaCodeEmbeddings()
    return _embedding_model

def _sanitize_text(text: str) -> str:
    """Remove NUL bytes and control characters."""
    text = text.replace("\x00", "")
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t", "\r") or (ord(ch) >= 32)
    )
    return text

def embed_chunks(chunks: list[dict]) -> list[dict]:
    """Add embedding vectors to each chunk."""
    model = get_embedding_model()
    embedded = []

    # Collect valid chunks for batch embedding
    valid_chunks = []
    valid_texts = []

    for chunk in chunks:
        text = chunk["text"]
        if not text or not text.strip():
            continue
        clean_text = _sanitize_text(text)

        if not clean_text.strip():
            continue

        valid_chunks.append(chunk)
        valid_texts.append(clean_text)
    
    if not valid_texts:
        return embedded
    
    # Batch embed in groups of 256 with progress
    EMBED_BATCH = 256
    total = len(valid_texts)
    _log(f"  Embedding {total} chunks (batch_size={EMBED_BATCH})...")

    for start in range(0, total, EMBED_BATCH):
        end = min(start + EMBED_BATCH, total)
        batch_texts = valid_texts[start:end]
        batch_chunks = valid_chunks[start:end]

        try:
            vectors = model.embed_documents(batch_texts)
            for chunk, vector in zip(batch_chunks, vectors):
                chunk["embedding"] = vector
                embedded.append(chunk)
        except Exception as e:
            _log(f"  ERROR batch embedding chunks {start}-{end}: {e}")
            for chunk, text in zip(batch_chunks, batch_texts):
                try:
                    vector = model.embed_query(text)
                    chunk["embedding"] = vector
                    embedded.append(chunk)
                except Exception as e2:
                    _log(f"  ERROR embedding {chunk['file_path']}::chunk{chunk['chunk_index']}: {e2}")

        _log(f"  Embedded {len(embedded)}/{total} chunks")

    return embedded
