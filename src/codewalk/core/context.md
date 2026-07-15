# `src/codewalk/core/` — Shared Graph Primitives

This package contains small reusable LangGraph building blocks used by the agent, research, and debug flows.

> **Scope**: This page covers `src/codewalk/core/`. It contains generic graph primitives only. Concrete agents live in `src/codewalk/agent`, `src/codewalk/research`, and `src/codewalk/debug`.

---

## 1. What is it?

`src/codewalk/core` is a library of three cross-cutting LangGraph utilities:

1. **`fanout.py`** — builds a parallel fan-out / fan-in graph from a state schema and a set of node callables.
2. **`reflect.py`** — runs a generic actor→critic→improve loop over any LLM output.
3. **`hitl.py`** — compiles any `StateGraph` with SQLite checkpointing and human-in-the-loop (HITL) interrupts, while keeping multi-threaded access safe.

None of these files know about reviews, research questions, or chat messages. They are pure graph-control primitives that higher-level modules compose.

---

## 2. Why does it exist?

Codewalk has several LangGraph-based pipelines: the chat agent, deep-research, and a debug fan-out agent. They all need the same three things:

- **Parallel sub-task execution** with a single merge/synthesize step.
- **Self-critique** of structured LLM outputs.
- **Persistent checkpoints + approval interrupts** that survive API worker threads.

Keeping these primitives in one place avoids duplicating graph wiring, reflection prompts, and SQLite thread-safety workarounds across `agent/`, `research/`, and `debug/`.

---

## 3. Data model

This package intentionally contains **no dataclasses or Pydantic models**. It operates on:

- `langgraph.graph.StateGraph`
- Plain `TypedDict` state schemas supplied by the caller
- Arbitrary Python objects passed through `reflect()`

If you are looking for the state schemas, they live with the consumers:

| State schema | Consumer file |
|--------------|---------------|
| `DebugState` | `src/codewalk/debug/fanout_agent.py` |
| `ResearchState` | `src/codewalk/research/deep_research.py` |
| `AgentState` | `src/codewalk/agent/graph.py` |

---

## 4. Step-by-step flow

### 4.1 `build_fanout_graph()` pipeline

```
START
  │
  ├──► parallel_node_1 ──┐
  ├──► parallel_node_2 ──┤
  ├──► parallel_node_N ──┘
  │                        │
  ▼                        ▼
                       merge_node
                          │
                          ▼
                      generate_node
                          │
                          ▼
                         END
```

```python
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
```

Each parallel node must write to a **different** key in `state_type`. The merge node reads all of those keys and writes a single merged key. The generate node reads the merged key and writes the final answer.

`build_fanout_graph()` returns an **uncompiled** `StateGraph`. The caller decides whether to compile it with `compile_with_hitl()`.

### 4.2 `reflect()` pipeline

```
initial_output
    │
    ▼
_build_critic_input(output, context)
    │
    ▼
critic LLM invoke(system=critic_system_prompt,
                  human=serialized_output+context)
    │
    ▼
parsed critique → improve_fn(output, critique)
    │
    ▼
repeat for N iterations or until "LGTM"
```

`reflect()` is generic: the caller supplies the `improve_fn`, the critic prompt, and the number of iterations. It does not depend on any specific output type.

### 4.3 `compile_with_hitl()` pipeline

```
StateGraph builder
    │
    ├──► async_checkpointer=True? ────────────────┐
    │                                               │
    │   No                                          │   Yes
    │   │                                           │
    ▼   ▼                                           ▼   ▼
sqlite3.connect(path)                    aiosqlite.connect(path)
SqliteSaver(conn)                        AsyncSqliteSaver(conn)
    │                                               │
    ▼                                               ▼
builder.compile(                           builder.compile(
    checkpointer=checkpointer,                 checkpointer=saver,
    interrupt_before=interrupt_nodes           interrupt_before=interrupt_nodes
)                                          )
    │                                               │
    ▼                                               ▼
_ThreadSafeGraph(compiled)                 returned via async with graph_ctx
```

---

## 5. How each operation is computed

### 5.1 `_ThreadSafeGraph`

```python
class _ThreadSafeGraph:
    def __init__(self, graph):
        self._graph = graph
        self._lock = threading.RLock()

    def invoke(self, *args, **kwargs):
        with self._lock:
            return self._graph.invoke(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._graph, name)
```

`_ThreadSafeGraph` wraps a compiled LangGraph graph and serializes access to its `invoke`, `stream`, `ainvoke`, and `astream` methods with an `RLock`. This is needed because `langgraph.checkpoint.sqlite.SqliteSaver` uses a single `sqlite3.Connection`, which is not thread-safe. Without the lock, concurrent API worker threads can corrupt the SQLite file or raise `SQLite objects created in a thread can only be used in that thread`.

Unknown attributes are forwarded to the underlying graph, so callers can still call `get_state()`, `update_state()`, etc.

### 5.2 `_AsyncGraphContext`

```python
class _AsyncGraphContext:
    def __init__(self, builder, interrupt_nodes, path):
        self._builder = builder
        self._interrupt_nodes = interrupt_nodes
        self._path = path
        self._conn = None
        self._saver = None
        self._graph = None

    async def __aenter__(self):
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
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
```

`_AsyncGraphContext` is an async context manager. It opens an `aiosqlite` connection, creates an `AsyncSqliteSaver`, compiles the graph, yields the compiled graph, and closes the connection on exit. This is required because `AsyncSqliteSaver` must live inside the same event loop that runs the graph.

### 5.3 `compile_with_hitl()`

```python
def compile_with_hitl(
    builder: StateGraph,
    interrupt_nodes: list[str],
    checkpoint_path: str | None = None,
    async_checkpointer: bool = False,
):
    path = checkpoint_path or ".codewalk/checkpoints.sqlite"

    if async_checkpointer:
        return _AsyncGraphContext(builder, interrupt_nodes, path)

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
```

`compile_with_hitl` centralizes two concerns:

1. **Persistence**: attaches an SQLite-backed checkpointer to the graph.
2. **HITL interrupts**: pauses execution before any node in `interrupt_nodes`.

The `interrupt_before=` parameter means the graph halts **before** the node runs, giving a human a chance to approve or reject the action.

---

## 6. Thresholds / configuration

| Constant / parameter | Default | Meaning |
|---|---|---|
| `checkpoint_path` | `.codewalk/checkpoints.sqlite` | SQLite file for checkpoints |
| `async_checkpointer` | `False` | Return async context manager instead of sync wrapper |
| `interrupt_nodes` | `[]` | Nodes to pause before; empty = persistence only, no interrupts |
| `check_same_thread=False` | hard-coded | Allows the sync `sqlite3` connection to be used across API worker threads (protected by `_ThreadSafeGraph` lock) |

---

## 7. How consumers use the result

| Consumer | File | Uses |
|---|---|---|
| Chat/voice agent | `src/codewalk/agent/graph.py` | `compile_with_hitl(..., interrupt_nodes=["write_tools"])` → sync `_ThreadSafeGraph` |
| Deep-research pipeline | `src/codewalk/research/deep_research.py` | `compile_with_hitl(..., async_checkpointer=True)` → async `_AsyncGraphContext` |
| Debug fan-out agent | `src/codewalk/debug/fanout_agent.py` | `compile_with_hitl(..., interrupt_nodes=[])` → sync `_ThreadSafeGraph` |

### 7.1 Sync usage (chat agent)

```python
# src/codewalk/agent/graph.py
graph = StateGraph(AgentState)
graph.add_node("agent", agent_node)
graph.add_node("read_tools", make_selective_tool_node(tools, read_tool_names))
graph.add_node("write_tools", make_selective_tool_node(tools, WRITE_TOOL_NAMES))
graph.add_edge(START, "agent")
graph.add_conditional_edges("agent", route_after_agent, ...)
graph.add_conditional_edges("read_tools", route_after_read, ...)
graph.add_edge("write_tools", "agent")

compiled = compile_with_hitl(
    graph,
    interrupt_nodes=["write_tools"],
    checkpoint_path=checkpoint_path,
)
# compiled is a _ThreadSafeGraph
```

### 7.2 Async usage (deep research)

```python
# src/codewalk/research/deep_research.py
builder = build_fanout_graph(
    state_type=ResearchState,
    parallel_nodes=parallel_nodes,
    merge_node=merge_findings,
    generate_node=make_synthesizer(graph_store),
)

graph_ctx = compile_with_hitl(
    builder,
    interrupt_nodes=interrupt_nodes,
    async_checkpointer=True,
)

async with graph_ctx as graph:
    result = await graph.ainvoke(initial_state, config={...})
```

---

## 8. Distinction from related files

### 8.1 `core/fanout.py` vs `debug/fanout_agent.py`

| | `core/fanout.py` | `debug/fanout_agent.py` |
|---|---|---|
| **Role** | Generic graph builder | Concrete experimental agent |
| **Returns** | Uncompiled `StateGraph` | Compiled `_ThreadSafeGraph` |
| **LLM usage** | None | Yes (search, git, blast radius, generate) |
| **Persistence** | None | SQLite checkpoint via `compile_with_hitl()` |

### 8.2 `core/hitl.py` vs `agent/graph.py`

| | `core/hitl.py` | `agent/graph.py` |
|---|---|---|
| **Role** | Generic checkpoint + interrupt compiler | Agent-specific nodes, routing, and state |
| **Graph knowledge** | None | Defines `agent_node`, `read_tools`, `write_tools`, routing |
| **HITL storage** | Provides `compile_with_hitl()` | Calls it; does not implement its own storage |

### 8.3 Sync vs async checkpointer

| | Sync (`async_checkpointer=False`) | Async (`async_checkpointer=True`) |
|---|---|---|
| **SQLite driver** | `sqlite3` | `aiosqlite` |
| **Saver class** | `SqliteSaver` | `AsyncSqliteSaver` |
| **Returned object** | `_ThreadSafeGraph` | `_AsyncGraphContext` |
| **Usage** | `graph.invoke(...)` | `async with graph_ctx as graph: await graph.ainvoke(...)` |
| **Thread safety** | `RLock` around every invocation | Single event loop; no extra lock needed |
| **Connection lifetime** | Open for process lifetime | Open only inside `async with` block |
| **Used by** | Chat agent, debug agent | Deep research |

---

## 8.4 FAQ: the questions that came up

### Does `state.get_modules_result()` use an LLM?

**No.** `modules_result` is produced by `detect_modules()` in `src/codewalk/analysis/module_detector.py`. It clusters files by directory heuristics and import structure — purely deterministic. The agent just reads the pre-computed dict.

### What does `compile_with_hitl()` actually return?

It returns **one of two things**, controlled by the `async_checkpointer` flag:

- `async_checkpointer=False` → a sync `_ThreadSafeGraph` (sqlite3 + `SqliteSaver`).
- `async_checkpointer=True` → an async context manager `_AsyncGraphContext` (aiosqlite + `AsyncSqliteSaver`).

Both paths compile the same `StateGraph` with `interrupt_before=interrupt_nodes` and SQLite checkpointing. The only difference is the SQLite driver and the wrapper type.

### Is the first call sync and later calls async?

**No.** The return type is deterministic from the flag. The chat agent always passes `async_checkpointer=False`, so it always gets a `_ThreadSafeGraph`. Deep research always passes `async_checkpointer=True`, so it always uses `_AsyncGraphContext`.

### What is inside `_AsyncGraphContext`?

- The uncompiled `StateGraph` builder.
- The list of interrupt node names.
- The SQLite path.
- An `aiosqlite` connection (`_conn`).
- An `AsyncSqliteSaver` (`_saver`).
- The compiled graph (`_graph`), created when you enter `async with`.

It is an async context manager because `AsyncSqliteSaver` must be created and torn down inside the event loop that runs the graph.

### What is `_ThreadSafeGraph`?

A thin wrapper around a compiled LangGraph graph. It holds:

- The compiled graph (`_graph`).
- An `RLock` (`_lock`).

Every invocation method (`invoke`, `stream`, `ainvoke`, `astream`) acquires the lock before delegating to the real graph. This makes the single sqlite3 connection safe across FastAPI worker threads. Unknown attributes are forwarded to the wrapped graph.

### So the app has two LangGraphs?

It has **three compiled graphs**, but only two are wired to production endpoints:

| Graph | Mode | Endpoint | State schema |
|---|---|---|---|
| Chat agent | Sync `_ThreadSafeGraph` | `/chat`, `/chat/stream`, `/chat/approve`, `/voice/ask` | `AgentState` |
| Deep research | Async `_AsyncGraphContext` | `/research` | `ResearchState` |
| Debug fan-out | Sync `_ThreadSafeGraph` | None today (experimental) | `DebugState` |

They are separate graphs with separate state schemas. They do not share checkpoints.

### If I switch from chat to deep research, do I lose chat state?

**No.** Chat state is persisted in `.codewalk/checkpoints.sqlite` under a `thread_id` for the chat graph. Deep research is a different graph and uses its own async checkpoint connection/state. Going back to chat resumes from the chat checkpoint. The two graphs do not interfere with each other.

### Is having sync + async graphs a good design?

**It is pragmatic, not perfect.**

- **Good**: chat needs persistent, thread-safe, multi-turn memory; deep research needs async I/O for parallel fan-out. Each mode gets the right tool.
- **Bad**: `compile_with_hitl()` returns two different types based on a boolean, which is confusing. You also maintain two SQLite driver paths (`sqlite3` and `aiosqlite`).
- **Better**: split into `compile_with_hitl_sync()` and `compile_with_hitl_async()` for clarity; or unify on async endpoints and a single `AsyncSqliteSaver` path.

### Does HITL do two things?

Yes. `compile_with_hitl` does exactly two things:

1. **Persistence**: attaches an SQLite checkpointer so graph state survives across turns.
2. **Interrupts**: pauses execution before any node in `interrupt_nodes` (e.g., `apply_fix`) so a human can approve or reject.

If `interrupt_nodes=[]`, you get persistence without interrupts.

---

## 9. Summary

### 9.1 End-to-end diagram

```
Uncompiled StateGraph (from fanout.py or custom)
    │
    ▼
compile_with_hitl(builder, interrupt_nodes, checkpoint_path, async_checkpointer)
    │
    ├──► async_checkpointer=False ────────┐
    │                                      │
    │   sqlite3 + SqliteSaver              │   aiosqlite + AsyncSqliteSaver
    │   compile(interrupt_before=...)      │   compile(interrupt_before=...)
    │   wrap in _ThreadSafeGraph           │   yield via _AsyncGraphContext
    │                                      │
    ▼                                      ▼
sync graph wrapper                    async graph context
    │                                      │
    ├──► agent/graph.py                    ├──► research/deep_research.py
    │      /chat, /chat/stream             │      POST /research
    │      /chat/approve, /voice/ask       │
    │                                      │
    └──► debug/fanout_agent.py            └──► (none today)
           (experimental)
```

### 9.2 Concept-to-file map

| Concept | Implementation | Source file |
|---|---|---|
| Fan-out graph builder | `build_fanout_graph()` | `src/codewalk/core/fanout.py` |
| Reflection loop | `reflect()` | `src/codewalk/core/reflect.py` |
| HITL compiler | `compile_with_hitl()` | `src/codewalk/core/hitl.py` |
| Sync graph wrapper | `_ThreadSafeGraph` | `src/codewalk/core/hitl.py` |
| Async graph context | `_AsyncGraphContext` | `src/codewalk/core/hitl.py` |
| Chat agent graph | `create_agent()` | `src/codewalk/agent/graph.py` |
| Deep-research graph | `_run_research_pipeline()` | `src/codewalk/research/deep_research.py` |
| Debug fan-out agent | `create_debug_agent()` | `src/codewalk/debug/fanout_agent.py` |
| API endpoint for chat | `/chat`, `/chat/stream`, `/chat/approve`, `/voice/ask` | `src/codewalk/api/main.py` |
| API endpoint for research | `/research` | `src/codewalk/api/main.py` |
| State lifecycle | `state.initialize()`, `state.get_agent()` | `src/codewalk/api/state.py` |

### 9.3 Trade-offs

**Why two checkpointer modes are reasonable:**

- Chat is conversational and persistent; sync `_ThreadSafeGraph` fits FastAPI worker threads.
- Deep research is one-shot and I/O-heavy; async `_AsyncGraphContext` fits parallel fan-out.
- The two graphs have different state schemas (`AgentState` vs `ResearchState`) and different lifecycles.

**Potential cleanup:**

1. The debug fan-out agent is not wired to production endpoints; consider removing it or promoting it to a real feature.
2. Deep research uses `asyncio.run()` inside `deep_research()`. If the caller ever becomes async, this will break. Prefer async endpoints that use `_AsyncGraphContext` directly.
3. `compile_with_hitl()` returning two different types based on a flag is flexible but can be confusing. Splitting it into `compile_with_hitl_sync()` and `compile_with_hitl_async()` could make call sites clearer.

In one sentence:

> `src/codewalk/core` provides reusable LangGraph primitives — fan-out graphs, reflection loops, and a unified HITL compiler — that the chat agent, deep-research pipeline, and debug agent compose into their own graph implementations.
