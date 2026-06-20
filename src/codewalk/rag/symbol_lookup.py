"""Deterministic symbol lookup for retrieval quality.

Uses the DuckDB knowledge graph to find exact symbol matches and their
callers/callees, then fetches the corresponding chunks from the vector store.
This helps location/caller questions where semantic search struggles.
"""
from __future__ import annotations

from src.codewalk.log import log as _log


def _symbol_chunks_from_names(
    store,
    symbol_names: set[str],
    file_hint: str | None = None,
) -> list[dict]:
    """Fetch parent chunks whose metadata matches the given symbol names."""
    if not store or not store.parents_collection or not symbol_names:
        return []

    results = []
    for name in symbol_names:
        where = {"symbol_name": name}
        if file_hint:
            where["file_path"] = file_hint
        try:
            hits = store.parents_collection.get(
                where=where,
                include=["metadatas", "documents"],
            )
            for doc, meta in zip(hits["documents"], hits["metadatas"]):
                results.append({
                    "text": doc,
                    "metadata": meta,
                    "distance": 0.0,  # exact match
                })
        except Exception as e:
            _log(f"[symbol_lookup] error fetching symbol {name}: {e}")
            continue

    return results


def lookup_symbol(
    graph_store,
    store,
    query: str,
    include_callers: bool = True,
    include_callees: bool = False,
) -> list[dict]:
    """Look up symbols mentioned in the query and return relevant chunks.

    Args:
        graph_store: GraphStore with symbol tables.
        store: VectorStore with parent/child chunks.
        query: User question (may contain symbol names).
        include_callers: If True, also fetch chunks for caller symbols.
        include_callees: If True, also fetch chunks for callee symbols.

    Returns:
        List of chunk dicts (text + metadata + distance=0.0).
    """
    if not graph_store or not store:
        return []

    # Extract candidate symbol names from the query: underscore/camelCase tokens.
    import re
    candidates = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query))
    # Filter out short/common words
    stopwords = {"the", "what", "how", "does", "is", "are", "where", "when",
                 "why", "who", "which", "this", "that", "these", "those",
                 "and", "or", "not", "but", "for", "with", "from", "to",
                 "in", "on", "at", "of", "a", "an", "it", "its"}
    candidates = {c for c in candidates if len(c) > 2 and c.lower() not in stopwords}

    if not candidates:
        return []

    # Query the graph for exact symbol name matches.
    matched_names: set[str] = set()
    caller_names: set[str] = set()
    callee_names: set[str] = set()

    try:
        rows = graph_store.conn.execute(
            "SELECT name, qualified_name FROM symbols WHERE name = ANY(?)",
            [list(candidates)],
        ).fetchall()
        matched_names = {row[0] for row in rows}

        if include_callers or include_callees:
            for name in matched_names:
                # Build qualified names from rows to look up callers/callees
                qualified_names = [row[1] for row in rows if row[0] == name]
                for qn in qualified_names:
                    if include_callers:
                        for caller in graph_store.get_callers_of_symbol(qn):
                            caller_names.add(caller["caller"])
                    if include_callees:
                        for callee in graph_store.get_callees_of_symbol(qn):
                            callee_names.add(callee["callee"])
    except Exception as e:
        _log(f"[symbol_lookup] graph query failed: {e}")
        return []

    all_names = matched_names | caller_names | callee_names
    if not all_names:
        return []

    chunks = _symbol_chunks_from_names(store, all_names)
    _log(f"[symbol_lookup] found {len(chunks)} chunks for symbols {all_names}")
    return chunks
