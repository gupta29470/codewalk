"""Deterministic symbol lookup service.

Wraps rag.symbol_lookup so both API and MCP use the same code path.
"""
from __future__ import annotations

from src.codewalk.rag.symbol_lookup import lookup_symbol


def lookup(
    query: str,
    store,
    graph_store=None,
    include_callers: bool = True,
    include_callees: bool = False,
) -> list[dict]:
    """Look up symbols mentioned in the query and return relevant chunks.

    Returns a list of chunk dicts (text + metadata + distance=0.0).
    """
    return lookup_symbol(graph_store, store, query, include_callers, include_callees)
