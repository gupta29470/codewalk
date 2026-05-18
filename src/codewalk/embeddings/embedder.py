"""
=============================================================================
 embedder.py — Text → Vector Conversion (Embeddings)
=============================================================================

WHAT THIS FILE DOES:
    Converts code text into numerical vectors (lists of floats).
    These vectors capture the MEANING of code — similar code produces
    similar vectors, enabling semantic search via ChromaDB.

    Example:
        embed("async function login()")  →  [0.12, -0.34, 0.56, ...]  (768 numbers)
        embed("authenticate user")       →  [0.11, -0.33, 0.55, ...]  (similar!)
        embed("sort array ascending")    →  [0.89, 0.02, -0.44, ...]  (different!)

HOW IT WORKS:
    1. Loads a pre-trained model (Jina Code Embeddings 1.5B) once at startup
    2. Detects best hardware: CUDA GPU > Apple MPS > CPU
    3. When called, feeds text through the neural network → gets a vector
    4. Normalizes vectors so cosine similarity works correctly

REAL-WORLD ANALOGY:
    Like GPS coordinates for code. "Login function" and "authentication handler"
    are at nearby GPS coordinates (similar meaning). "Image resizer" is in a
    completely different location. ChromaDB finds nearby coordinates = similar code.

WHY JINA CODE EMBEDDINGS (not OpenAI)?
    - Runs LOCAL — no API calls, no cost, no data leaving your machine
    - Trained specifically on CODE — understands programming structure
    - "def main()" and "entry point" are close in Jina's space, but far apart
      in generic English models

WHO CALLS THIS:
    - chunker.py → after splitting code into chunks, embed_chunks() vectorizes them
    - vector_store.py → search() uses embed_query() to vectorize the search query

DEPENDENCIES:
    - sentence_transformers: HuggingFace library that runs embedding models
    - torch: PyTorch — needed for GPU acceleration (MPS on Mac)
    - config.py: provides the model name setting

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import torch  # PyTorch — used only for GPU detection (cuda/mps)
from sentence_transformers import SentenceTransformer  # Runs the embedding model
from langchain_core.embeddings import Embeddings  # Interface that ChromaDB expects
from src.codewalk.config import settings
from src.codewalk.log import log as _log


# =============================================================================
# _detect_device() — Find the Best Available Hardware
# =============================================================================

def _detect_device() -> str:
    """Auto-detect the best compute device for running the embedding model.

    PRIORITY ORDER:
        1. "cuda" — NVIDIA GPU (fastest, 10-100x speed)
        2. "mps"  — Apple Silicon GPU (M1/M2/M3 Mac, 5-20x speed)
        3. "cpu"  — Fallback (slowest but always works)

    WHY THIS MATTERS:
        Embedding 15,000 code chunks:
          - CPU: ~30 minutes
          - MPS (M2 Mac): ~3 minutes
          - CUDA (RTX 4090): ~30 seconds
    """
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"  # Apple Silicon (M1/M2/M3)
    return "cpu"


# =============================================================================
# JinaCodeEmbeddings — The Embedding Model Wrapper
# =============================================================================

class JinaCodeEmbeddings(Embeddings):
    """LangChain-compatible wrapper around Jina's code embedding model.

    WHY A WRAPPER?
        ChromaDB and LangChain expect an object with:
          .embed_query(text) → single vector
          .embed_documents([texts]) → list of vectors

        SentenceTransformer uses different method names (.encode()).
        This class bridges the gap — translates LangChain's interface
        to SentenceTransformer's interface.

    WHAT HAPPENS AT __init__:
        1. Picks model name from settings (default: jinaai/jina-code-embeddings-1.5b)
        2. Detects hardware (cuda/mps/cpu)
        3. Downloads model from HuggingFace if not cached (~1.5GB first time)
        4. Loads model into memory on the detected device
        5. Ready to convert text → vectors

    HOW NORMALIZE_EMBEDDINGS WORKS:
        Makes all vectors unit length (magnitude = 1.0).
        This ensures cosine similarity works correctly:
          - Without normalization: longer documents get artificially higher scores
          - With normalization: only DIRECTION matters, not magnitude
    """

    def __init__(self, model_name: str = "", device: str = ""):
        model_name = model_name or settings.embedding_model
        device = device or _detect_device()
        _log(f"Loading embedding model: {model_name} (device={device})")
        # SentenceTransformer downloads + loads the model
        # trust_remote_code=True: allows custom model code from HuggingFace
        self._model = SentenceTransformer(
            model_name,
            device=device,
            trust_remote_code=True,
        )
        _log(f"Embedding model loaded: {model_name}")
    
    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query into a vector.

        Used when SEARCHING — you have one query to vectorize.
        Example: embed_query("authentication logic") → [0.12, -0.34, ...]
        """
        vectors = self._model.encode(text, normalize_embeddings=True)
        return vectors.tolist()  # numpy array → Python list
    
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple documents at once (batch processing).

        Used when INDEXING — you have thousands of code chunks to vectorize.
        Batching (batch_size=32) is much faster than one-by-one because
        the GPU can process 32 texts simultaneously.
        """
        vectors = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
        return vectors.tolist()


# =============================================================================
# Singleton Pattern — Load Model Once
# =============================================================================

# The model takes 5-10 seconds to load and uses ~1.5GB RAM.
# We load it ONCE and reuse it for all operations.
_embedding_model: JinaCodeEmbeddings | None = None

def get_embedding_model() -> Embeddings:
    """Returns the embedding model (singleton — loads once, reused forever).

    FIRST CALL: Downloads model (if needed) + loads into GPU memory (~5s)
    SUBSEQUENT CALLS: Returns the already-loaded instance instantly
    """
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = JinaCodeEmbeddings()
    return _embedding_model


# =============================================================================
# _sanitize_text() — Clean Text Before Embedding
# =============================================================================

def _sanitize_text(text: str) -> str:
    """Remove characters that would corrupt the embedding or ChromaDB storage.

    WHAT IT REMOVES:
        - NUL bytes (\x00): crash ChromaDB's SQLite backend
        - Control characters (ASCII 0-31): confuse tokenizers

    WHAT IT KEEPS:
        - Newlines, tabs, carriage returns (valid in code)
        - All printable characters (ASCII 32+)
    """
    text = text.replace("\x00", "")
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t", "\r") or (ord(ch) >= 32)
    )
    return text


# =============================================================================
# embed_chunks() — The Main Entry Point
# =============================================================================

def embed_chunks(chunks: list[dict]) -> list[dict]:
    """Add embedding vectors to each chunk dict.

    EXECUTION FLOW:
        1. Receives chunks from chunker.py: [{"text": "def login()...", "file_path": "auth.py", ...}, ...]
        2. Filters out empty/whitespace-only chunks
        3. Sanitizes text (remove NUL bytes)
        4. Batch-embeds in groups of 256 (GPU processes 256 texts at once)
        5. Adds "embedding" key to each chunk: chunk["embedding"] = [0.12, -0.34, ...]
        6. Returns only successfully embedded chunks

    WHY BATCH SIZE 256?
        - Too small (1): wastes GPU parallelism, very slow
        - Too large (10000): runs out of GPU memory, crashes
        - 256: sweet spot for most GPUs with code embeddings

    ERROR HANDLING:
        If a batch fails (e.g., one text is too long for the model):
        - Falls back to embedding each text individually
        - Logs which specific chunk failed
        - Never crashes the whole pipeline for one bad chunk
    """
    model = get_embedding_model()
    embedded = []

    # ── Step 1: Filter out empty chunks ──
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
    
    # ── Step 2: Batch embed with progress logging ──
    EMBED_BATCH = 256
    total = len(valid_texts)
    _log(f"  Embedding {total} chunks (batch_size={EMBED_BATCH})...")

    for start in range(0, total, EMBED_BATCH):
        end = min(start + EMBED_BATCH, total)
        batch_texts = valid_texts[start:end]
        batch_chunks = valid_chunks[start:end]

        try:
            # Fast path: embed entire batch at once
            vectors = model.embed_documents(batch_texts)
            for chunk, vector in zip(batch_chunks, vectors):
                chunk["embedding"] = vector
                embedded.append(chunk)
        except Exception as e:
            # Slow fallback: one at a time to find the problematic text
            _log(f"  ERROR batch embedding chunks {start}-{end}: {e}")
            for chunk, text in zip(batch_chunks, batch_texts):
                try:
                    vector = model.embed_query(text)
                    chunk["embedding"] = vector
                    embedded.append(chunk)
                except Exception as e2:
                    _log(f"  ERROR embedding {chunk[\'file_path\']}::chunk{chunk[\'chunk_index\']}: {e2}")

        _log(f"  Embedded {len(embedded)}/{total} chunks")

    return embedded
