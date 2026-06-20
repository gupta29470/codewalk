from __future__ import annotations
import os
import sqlite3
import threading
from langgraph.graph import StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver


class _ThreadSafeGraph:
    """Wrap a compiled LangGraph graph so SQLite checkpoint access is serialized.

    LangGraph's SqliteSaver uses a single sqlite3.Connection. Without locking,
    concurrent threads invoking the same compiled graph can corrupt the SQLite
    file (or raise "SQLite objects created in a thread can only be used in that
    thread"). This wrapper acquires an RLock around every invocation method.
    """

    def __init__(self, graph):
        self._graph = graph
        self._lock = threading.RLock()

    def invoke(self, *args, **kwargs):
        with self._lock:
            return self._graph.invoke(*args, **kwargs)

    def stream(self, *args, **kwargs):
        with self._lock:
            yield from self._graph.stream(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        with self._lock:
            return await self._graph.ainvoke(*args, **kwargs)

    async def astream(self, *args, **kwargs):
        with self._lock:
            async for item in self._graph.astream(*args, **kwargs):
                yield item

    def __getattr__(self, name):
        return getattr(self._graph, name)


def compile_with_hitl(builder: StateGraph,
    interrupt_nodes: list[str], checkpoint_path: str | None = None):
    """Compile any LangGraph graph with persistent checkpointing + human-in-the-loop interrupts.

    Args:
        builder:          A StateGraph instance with all nodes/edges added but not yet compiled.
        interrupt_nodes:  Node names to pause BEFORE. Graph halts before executing these nodes.
                          e.g. ["apply_fix", "create_pr"] or [] for no interrupts (just persistence).
        checkpoint_path:  Path to SQLite file. Defaults to `.codewalk/checkpoints.sqlite`.
                          Pass an explicit path in tests to use a temp file.

    Returns:
        Compiled LangGraph graph ready to invoke (thread-safe wrapper).
    """
    path = checkpoint_path or ".codewalk/checkpoints.sqlite"

    # Ensure parent directory exists so sqlite3 can create the file
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    compiled = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_nodes
    )
    return _ThreadSafeGraph(compiled)