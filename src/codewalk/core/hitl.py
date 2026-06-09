from __future__ import annotations
import os
import sqlite3
from langgraph.graph import StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

def compile_with_hitl(builder: StateGraph,
    interrupt_nodes: list[str], checkpoint_path: str | None = None):
    """Compile any LangGraph graph with persistent checkpointing + human-in-the-loop interrupts.

    Args:
        builder:          A StateGraph instance with all nodes/edges added but not yet compiled.
        interrupt_nodes:  Node names to pause BEFORE. Graph halts before executing these nodes.
                          e.g. ["apply_fix", "create_pr"] or [] for no interrupts (just persistence).
        checkpoint_path:  Path to SQLite file. Defaults to settings.CHECKPOINT_DB_PATH.
                          Pass an explicit path in tests to use a temp file.

    Returns:
        Compiled LangGraph graph ready to invoke.
    """
    from src.codewalk.config import settings
    path = checkpoint_path or settings.checkpoint_db_path

    # Ensure parent directory exists so sqlite3 can create the file
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes
    )