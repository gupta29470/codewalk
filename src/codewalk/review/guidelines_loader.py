import os
from pathlib import Path

from src.codewalk.embeddings.vector_store import VectorStore

GUIDELINE_EXTENSIONS = {".md", ".txt", ".rst"}

def load_guidelines(guidelines_path: str) -> list[dict]:
    """Read all guideline files (.md, .txt, .rst) from the guidelines folder.

    Returns list of {"text": content, "metadata": {"source": filename}}
    """
    path = Path(guidelines_path)
    if not path.exists():
        return []
    
    docs = []
    for doc_file in path.rglob("*"):
        if not doc_file.is_file():
            continue
        if doc_file.suffix.lower() not in GUIDELINE_EXTENSIONS:
            continue
        content = doc_file.read_text(encoding="utf-8").strip()
        if content:
            docs.append({
                "text": content,
                "metadata": {
                    "source": str(doc_file.relative_to(path)),
                    "type": "guideline",
                },
            })
    return docs

def get_guidelines_store() -> VectorStore | None:
    """Get or create the guidelines vector store.

    Returns None if REVIEW_GUIDELINES_PATH is not set.
    Embeds guidelines on first call, reuses on subsequent calls.

    Guidelines are stored as "leftover" chunks (no parent-child split)
    in a separate ChromaDB instance alongside the guidelines folder.
    """
    guidelines_path = os.getenv("REVIEW_GUIDELINES_PATH", "")
    if not guidelines_path:
        return None
    
    persist_dir = os.path.join(guidelines_path, ".codewalk_index")
    store = VectorStore(persist_dir=persist_dir)
    store.create_collection("guidelines")

    existing = store.chunk_count()
    if existing > 0:
        return store
    
    # First time — load, chunk, embed, store
    docs = load_guidelines(guidelines_path)
    if not docs:
        return None
    
    chunks = []
    for doc_idx, doc in enumerate(docs):
        text = doc["text"]
        source = doc["metadata"]["source"]

        if len(text) < 2000:
            chunks.append({
                "text": text,
                "file_path": source,
                "language": "markdown",
                "chunk_index": doc_idx,
                "chunk_type": "leftover",
                "parent_chunk_id": None,
                "source": "guidelines",
            })
        else:
            # Split by ## headers for large docs
            sections = text.split("\n## ")
            for sec_idx, section in enumerate(sections):
                if sec_idx > 0:
                    section = "## " + section
                chunks.append({
                    "text": section.strip(),
                    "file_path": source,
                    "language": "markdown",
                    "chunk_index": f"{doc_idx}_{sec_idx}",
                    "chunk_type": "leftover",
                    "parent_chunk_id": None,
                    "source": "guidelines",
                })

    from src.codewalk.embeddings.embedder import embed_chunks
    embedded = embed_chunks(chunks)
    store.add_parent_child_chunks(embedded)
    return store

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


