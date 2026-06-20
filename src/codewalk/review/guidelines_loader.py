import os
from pathlib import Path

from src.codewalk.embeddings.vector_store import VectorStore


def get_guidelines_store(
    guidelines_path: str = "",
    persist_dir: str = "",
    force: bool = False,
) -> VectorStore | None:
    """Get or create the guidelines vector store.

    ``guidelines_path`` is read from ``codewalk.yaml`` by the caller.

    If no path is passed but a non-empty ``guidelines`` collection already
    exists in ChromaDB, the existing store is returned so previously indexed
    guidelines stay available.

    Args:
        guidelines_path: Folder containing guideline .md/.txt/.rst/.pdf files.
        persist_dir:     ChromaDB directory (e.g. {repo}/.codewalk/chroma/).
        force:           If True, clear existing and re-embed from scratch.

    Returns None if no guidelines path is available and no indexed collection
    exists. Embeds guidelines on first call, reuses on subsequent calls.
    """
    # Default persist_dir: repo's .codewalk/chroma/ (same ChromaDB as code)
    if not persist_dir:
        from src.codewalk.config import settings
        repo = getattr(settings, "repo_path", "") or "."
        persist_dir = os.path.join(repo.rstrip("/"), ".codewalk", "chroma")

    chroma_dir = persist_dir
    store = VectorStore(persist_dir=chroma_dir)
    store.create_collection("guidelines")

    existing = store.chunk_count()

    # No path configured, but an indexed collection exists -> reuse it.
    if not guidelines_path:
        return store if existing > 0 else None

    if existing > 0 and not force:
        return store

    # Force reindex — wipe existing chunks
    if force and existing > 0:
        store.clear_collection()

    # Load, chunk, embed, store — reuse the docs parser so PDFs are supported too.
    from src.codewalk.doc_knowledge.doc_parser import parse_all_docs
    from src.codewalk.embeddings.embedder import embed_chunks

    chunks = parse_all_docs(guidelines_path)

    # The doc parser drops very short sections; for guidelines, fall back to
    # reading each supported file as a single chunk so small rules aren't lost.
    if not chunks:
        chunks = _read_guideline_files_raw(guidelines_path)
        if not chunks:
            return None

    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        chunk["file_path"] = metadata.get("doc_path", "guideline")
        chunk["chunk_index"] = metadata.get("chunk_index", 0)
        chunk["chunk_type"] = "leftover"
        chunk["parent_chunk_id"] = None
        chunk["source"] = "guidelines"
        chunk["language"] = "markdown"

    embedded = embed_chunks(chunks)
    store.add_parent_child_chunks(embedded)
    return store


def _read_guideline_files_raw(guidelines_path: str) -> list[dict]:
    """Read all supported guideline files as single chunks."""
    from pathlib import Path

    GUIDELINE_EXTENSIONS = {".md", ".txt", ".rst", ".pdf"}
    path = Path(guidelines_path)
    if not path.exists():
        return []

    chunks = []
    for doc_file in path.rglob("*"):
        if not doc_file.is_file():
            continue
        if doc_file.suffix.lower() not in GUIDELINE_EXTENSIONS:
            continue
        if doc_file.suffix.lower() == ".pdf":
            from src.codewalk.doc_knowledge.doc_parser import parse_pdf
            pdf_chunks = parse_pdf(str(doc_file), str(doc_file.relative_to(path)))
            chunks.extend(pdf_chunks)
            continue
        content = doc_file.read_text(encoding="utf-8").strip()
        if content:
            chunks.append({
                "text": content,
                "metadata": {
                    "doc_path": str(doc_file.relative_to(path)),
                    "chunk_index": 0,
                },
            })
    return chunks


def search_guidelines(store: VectorStore, diff_files: list, n_results: int = 3) -> str:
    """Search guidelines relevant to the changed files.

    Builds a query from changed file paths + languages,
    retrieves matching guidelines, formats for the LLM prompt.
    """
    if not store:
        return ""

    # Build query from file context
    languages = set(diff_file.language for diff_file in diff_files if diff_file.language != "unknown")
    file_paths = [df.file_path for df in diff_files[:5]]
    query = f"coding guidelines for {', '.join(languages)} files: {', '.join(file_paths)}"

    results = store.search(query, n_results=n_results)
    if not results:
        return ""

    # Format for prompt injection
    lines = ["## Team Coding Guidelines"]
    for doc in results:
        source = doc.get("metadata", {}).get("source", "unknown")
        lines.append(f"\n### From: {source}")
        lines.append(doc["text"])

    return "\n".join(lines)
