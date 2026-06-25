"""Parallel fan-out/fan-in graph utilities for multi-angle research."""
from __future__ import annotations
from collections.abc import Callable
from langgraph.graph import StateGraph, START, END

def build_fanout_graph(state_type: type, 
    parallel_nodes: dict[str, Callable], 
    merge_node: Callable, 
    generate_node: Callable) -> StateGraph:
    """Build a fan-out/fan-in StateGraph.

    All parallel_nodes start from START simultaneously.
    Each node must write to a DIFFERENT field in state_type.
    merge_node combines all parallel results.
    generate_node produces the final output from merged context.

    Returns an UNCOMPILED StateGraph — caller decides checkpointing.
    Compose with compile_with_hitl() from core/hitl.py if needed.

    Args:
        state_type:     TypedDict class with a separate field per parallel node.
        parallel_nodes: Dict of node_name → async callable.
                        Each callable: (state) → dict of fields it writes.
        merge_node:     Async callable. Reads all parallel output fields,
                        writes a single merged field (e.g. merged_context).
        generate_node:  Async callable. Reads merged output, writes final answer.

    Returns:
        Uncompiled StateGraph builder.
    """
    builder = StateGraph(state_type)

    for name, fn in parallel_nodes.items():
        builder.add_node(name, fn)
        builder.add_edge(START, name)
        builder.add_edge(name, "merge")

    builder.add_node("merge", merge_node)
    builder.add_node("generate", generate_node)
    builder.add_edge("merge", "generate")
    builder.add_edge("generate", END)

    return builder