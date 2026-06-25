"""Sentence-transformer embedding model wrapper for code chunks."""
import gc
import torch
from sentence_transformers import SentenceTransformer
from langchain_core.embeddings import Embeddings
from src.codewalk.config import settings
from src.codewalk.log import log as _log

_MAX_SEQ_LENGTH = 4096

_MAX_CHUNK_CHARS = 15_000

def _detect_device() -> str:
    """Auto-detect the best available compute device."""
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"

def _clear_gpu_cache():
    """Free GPU memory after errors or between large batches."""
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.empty_cache()

class JinaCodeEmbeddings(Embeddings):
    """LangChain-compatible wrapper around Jina code embedding model.

    Implements embed_query() and embed_documents() so VectorStore
    and ChromaDB work without any changes.
    """

    def __init__(self, model_name: str = "", device: str = ""):
        model_name = model_name or settings.embedding_model
        device = device or _detect_device()
        self._device = device
        _log(f"Loading embedding model: {model_name} (device={device})")
        model_kwargs = {"dtype": torch.float16} if device != "cpu" else {}
        self._model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
            model_kwargs=model_kwargs,
        )
        # Cap max sequence length to prevent OOM from huge attention matrices.
        # Attention is O(seq_len²) — 32K tokens → 4x memory vs 16K.
        orig = getattr(self._model, "max_seq_length", None)
        if orig and orig > _MAX_SEQ_LENGTH:
            self._model.max_seq_length = _MAX_SEQ_LENGTH
            _log(f"  Capped max_seq_length: {orig} → {_MAX_SEQ_LENGTH}")
        _log(f"Embedding model loaded: {model_name}")
    
    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        text = text[:_MAX_CHUNK_CHARS]
        vectors = self._model.encode(text, normalize_embeddings=True)
        return vectors.tolist()
    
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of documents (batch)."""
        texts = [t[:_MAX_CHUNK_CHARS] for t in texts]
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
    """Add embedding vectors to each chunk.

    Parent chunks are skipped — they go to parents_collection which is
    only fetched by ID (.get()), never vector-searched (.query()).
    Skipping their embedding cuts GPU time roughly in half.
    """
    model = get_embedding_model()

    # ── Separate parent chunks (context-only, never vector-searched) ──
    parents = []
    embeddable = []

    for chunk in chunks:
        text = chunk.get("text", "")
        if not text or not text.strip():
            continue
        if chunk.get("chunk_type") == "parent":
            parents.append(chunk)
        else:
            clean_text = _sanitize_text(text)
            if clean_text.strip():
                embeddable.append((chunk, clean_text))

    # Assign zero embeddings to parents — valid for ChromaDB storage,
    # never used in search (parents are fetched by ID only).
    if parents:
        dim = model._model.get_embedding_dimension()
        zero_vec = [0.0] * dim
        for chunk in parents:
            chunk["embedding"] = zero_vec

    if not embeddable:
        return parents

    # Sort by text length so similar-sized chunks batch together.
    # Eliminates padding waste — short chunks aren't padded to the
    # length of a long outlier in the same batch.
    embeddable.sort(key=lambda x: len(x[1]))
    sorted_chunks = [e[0] for e in embeddable]
    sorted_texts = [e[1] for e in embeddable]

    EMBED_BATCH = 64
    total = len(sorted_texts)
    _log(f"  Embedding {total} chunks (batch={EMBED_BATCH}, {len(parents)} parents skipped)...")

    embedded = []
    batch_num = 0
    for start in range(0, total, EMBED_BATCH):
        end = min(start + EMBED_BATCH, total)
        batch_texts = sorted_texts[start:end]
        batch_chunks = sorted_chunks[start:end]

        try:
            vectors = model.embed_documents(batch_texts)
            for chunk, vector in zip(batch_chunks, vectors):
                chunk["embedding"] = vector
                embedded.append(chunk)
        except Exception as e:
            _log(f"  ERROR batch embedding chunks {start}-{end}: {e}")
            _clear_gpu_cache()
            _log(f"  Retrying in mini-batches of 4...")
            for mini_start in range(0, len(batch_texts), 4):
                mini_end = min(mini_start + 4, len(batch_texts))
                mini_texts = batch_texts[mini_start:mini_end]
                mini_chunks = batch_chunks[mini_start:mini_end]
                try:
                    vectors = model.embed_documents(mini_texts)
                    for chunk, vector in zip(mini_chunks, vectors):
                        chunk["embedding"] = vector
                        embedded.append(chunk)
                except Exception:
                    _clear_gpu_cache()
                    for chunk, text in zip(mini_chunks, mini_texts):
                        try:
                            vector = model.embed_query(text)
                            chunk["embedding"] = vector
                            embedded.append(chunk)
                        except Exception as e2:
                            _clear_gpu_cache()
                            _log(f"  SKIP {chunk['file_path']}::chunk{chunk['chunk_index']}: {e2}")

        _log(f"  Embedded {len(embedded)}/{total} chunks")

        # Periodic GPU cleanup to prevent MPS memory fragmentation.
        batch_num += 1
        if batch_num % 20 == 0:
            _clear_gpu_cache()

    return parents + embedded
