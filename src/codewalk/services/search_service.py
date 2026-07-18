"""Deterministic codebase search service.

No LLM calls. Combines:
  - exact symbol lookup via the knowledge graph
  - semantic search via ChromaDB
  - distance-based filtering
  - free keyword grading
  - graph expansion fallback

Used by MCP tools and by API endpoints that want raw retrieval.
"""
from __future__ import annotations

from src.codewalk.rag.symbol_lookup import lookup_symbol
from src.codewalk.rag.retrieval_quality import filter_by_distance, is_retreival_good
from src.codewalk.rag.chunk_grader import grade_chunks_free
from src.codewalk.rag.graph_expansion import expand_via_graph
from src.codewalk.log import log as _log


def _deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    """Deduplicate chunks by file + symbol + line range."""
    seen = set()
    unique = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        key = (
            meta.get("file_path", ""),
            meta.get("symbol_name", ""),
            meta.get("start_line", 0),
            meta.get("end_line", 0),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


def _dynamic_n_results(question: str, base: int = 5) -> int:
    """Choose retrieval depth based on query complexity."""
    lowered = question.lower()
    overview_indicators = ["overview", "summary", "architecture", "how does", "explain", "flow"]
    if any(ind in lowered for ind in overview_indicators):
        return max(base, 12)
    return base


def search(
    query: str,
    store,
    graph_store=None,
    n_results: int = 5,
    use_graph_expansion: bool = True,
) -> tuple[list[dict], float, bool]:
    """Deterministic search: symbol → semantic → distance filter → keyword → graph.

    Returns:
        (chunks, confidence, retrieval_good)
    """
    n_results = _dynamic_n_results(query, n_results)
    all_chunks: list[dict] = []

    # Layer 0: deterministic symbol lookup
    symbol_chunks = lookup_symbol(graph_store, store, query)
    if symbol_chunks:
        all_chunks.extend(symbol_chunks)
        _log(f"[search_service] symbol lookup added {len(symbol_chunks)} chunks")

    # Layer 1: semantic search
    semantic_results = store.search(query, n_results=n_results)
    if semantic_results:
        all_chunks.extend(semantic_results)

    if not all_chunks:
        return [], 0.0, False

    all_chunks = _deduplicate_chunks(all_chunks)
    filtered, confidence = filter_by_distance(all_chunks)
    retrieval_good = is_retreival_good(confidence, len(filtered))

    # Layer 2: graph expansion fallback (deterministic)
    if use_graph_expansion and not retrieval_good and graph_store and filtered:
        expanded = expand_via_graph(filtered, store, query, graph_store)
        if len(expanded) > len(filtered):
            filtered = expanded
            confidence = max(confidence, 0.35)
            retrieval_good = is_retreival_good(confidence, len(filtered))
            _log(f"[search_service] graph expansion recovered {len(expanded)} chunks")

    if not filtered:
        filtered = all_chunks

    # Layer 3: free keyword grading (deterministic)
    graded = grade_chunks_free(query, filtered)
    if graded:
        filtered = graded

    return filtered, confidence, retrieval_good
