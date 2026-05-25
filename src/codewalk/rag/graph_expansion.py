"""Graph-augmented retrieval fallback.

When semantic search retrieves chunks but they're not relevant enough,
expand search to neighboring files using the igraph dependency graph.

Zero LLM cost — just additional ChromaDB queries guided by graph edges.
"""

from src.codewalk.log import log as _log


def expand_via_graph(
    results: list[dict],
    store,
    query: str,
    graph_store=None,
    n_extra: int = 5,
) -> list[dict]:
    """Expand retrieval by searching neighboring files via the dependency graph.

    Takes the files from initial retrieval results, finds their imports
    and importers via DuckDB, then searches for the query within those
    neighboring files.

    Args:
        results: Initial search results (may be weak/irrelevant).
        store: VectorStore for additional searches.
        query: Original user query.
        graph_store: GraphStore (DuckDB) for file-level imports.
        n_extra: How many additional chunks to retrieve from neighbors.

    Returns:
        Combined list: original results + neighbor results (deduplicated).
    """
    if not graph_store or not results:
        return results

    # Collect file paths from initial results
    source_files = set()
    for result in results:
        fp = result.get("metadata", {}).get("file_path")
        if fp:
            source_files.add(fp)

    if not source_files:
        return results

    # Find neighboring files via graph edges (imports + importers)
    neighbor_files = set()
    for fp in source_files:
        for imported in graph_store.get_imports(fp):
            neighbor_files.add(imported)
        for importer in graph_store.get_importers(fp):
            neighbor_files.add(importer)

    # Remove files we already have results for
    neighbor_files -= source_files

    if not neighbor_files:
        _log("[graph_expansion] No neighbor files found — skipping")
        return results

    _log(f"[graph_expansion] Expanding to {len(neighbor_files)} neighbor files from {len(source_files)} source files")

    # Search within neighbor files
    neighbor_results = []
    for fp in sorted(neighbor_files)[:10]:  # Cap at 10 neighbors to limit queries
        try:
            file_results = store.parents_collection.get(
                where={"file_path": fp},
                include=["documents", "metadatas"],
            )
            if file_results["documents"]:
                for doc, meta in zip(file_results["documents"], file_results["metadatas"]):
                    neighbor_results.append({
                        "text": doc,
                        "metadata": meta,
                        "distance": 0.5,  # neutral distance — let chunk grader decide
                    })
        except Exception as e:
            _log(f"[graph_expansion] Error querying neighbor {fp}: {e}")
            continue

    if not neighbor_results:
        _log("[graph_expansion] No chunks found in neighbor files")
        return results

    # Deduplicate by file_path + symbol_name
    seen = set()
    for r in results:
        meta = r.get("metadata", {})
        key = (meta.get("file_path", ""), meta.get("symbol_name", ""), meta.get("start_line", 0))
        seen.add(key)

    unique_neighbors = []
    for r in neighbor_results:
        meta = r.get("metadata", {})
        key = (meta.get("file_path", ""), meta.get("symbol_name", ""), meta.get("start_line", 0))
        if key not in seen:
            seen.add(key)
            unique_neighbors.append(r)

    combined = results + unique_neighbors[:n_extra]
    _log(f"[graph_expansion] Added {len(unique_neighbors[:n_extra])} neighbor chunks (total: {len(combined)})")
    return combined
