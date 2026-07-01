"""Human-in-the-loop interrupt utilities for LangGraph agents."""
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


class _AsyncGraphContext:
    """Async context manager for a graph compiled with AsyncSqliteSaver.

    AsyncSqliteSaver must be created and kept alive inside the same event loop
    that runs the graph. This context manager opens the aiosqlite connection,
    compiles the graph, yields it, and closes the connection on exit.
    """

    def __init__(self, builder: StateGraph, interrupt_nodes: list[str], path: str):
        self._builder = builder
        self._interrupt_nodes = interrupt_nodes
        self._path = path
        self._conn = None
        self._saver = None
        self._graph = None

    async def __aenter__(self):
        # Delay the import so this module stays importable when aiosqlite is absent.
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._conn = aiosqlite.connect(self._path)
        await self._conn.__aenter__()
        self._saver = AsyncSqliteSaver(self._conn)
        self._graph = self._builder.compile(
            checkpointer=self._saver,
            interrupt_before=self._interrupt_nodes,
        )
        return self._graph

    async def __aexit__(self, exc_type, exc, tb):
        if self._conn is not None:
            return await self._conn.__aexit__(exc_type, exc, tb)
        return False


def compile_with_hitl(builder: StateGraph,
    interrupt_nodes: list[str], checkpoint_path: str | None = None,
    async_checkpointer: bool = False):
    """Compile any LangGraph graph with persistent checkpointing + human-in-the-loop interrupts.

    Args:
        builder:          A StateGraph instance with all nodes/edges added but not yet compiled.
        interrupt_nodes:  Node names to pause BEFORE. Graph halts before executing these nodes.
                          e.g. ["apply_fix", "create_pr"] or [] for no interrupts (just persistence).
        checkpoint_path:  Path to SQLite file. Defaults to `.codewalk/checkpoints.sqlite`.
                          Pass an explicit path in tests to use a temp file.
        async_checkpointer: If True, return an async context manager that yields a graph
                          compiled with AsyncSqliteSaver. The caller must use `async with`.
                          Use this when invoking the graph via `.ainvoke()` / `.astream()`.

    Returns:
        Compiled LangGraph graph ready to invoke (thread-safe wrapper) when
        async_checkpointer=False; an async context manager when async_checkpointer=True.
    """
    path = checkpoint_path or ".codewalk/checkpoints.sqlite"

    if async_checkpointer:
        return _AsyncGraphContext(builder, interrupt_nodes, path)

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
