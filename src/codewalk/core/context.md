# `src/codewalk/core/` — Shared Graph Primitives

This package contains small reusable LangGraph building blocks used by the agent and research flows.

## Modules

| File | Role |
|------|------|
| `fanout.py` | `build_fanout_graph()` — runs N parallel sub-tasks (e.g. research sub-questions) and aggregates results. |
| `reflect.py` | Reflection/critic node utilities for self-critique loops. |
| `hitl.py` | Human-in-the-loop interrupt utilities used by `agent/graph.py` and `agent/tools.py`.

## Connections

- `research/` uses `core/fanout.py` and `core/reflect.py` to run multi-angle research and then reflect on the report.
- `agent/graph.py` uses `core/hitl.py` for approval interrupts.

## Recent fixes

- `core/hitl.py` wraps compiled LangGraph graphs in `_ThreadSafeGraph`, serializing `invoke`/`stream`/`ainvoke`/`astream` with an `RLock` so the SQLite checkpointer is not accessed concurrently from multiple API worker threads. It also supports `async_checkpointer=True` (used by `research/deep_research.py`) which returns an async context wrapping `AsyncSqliteSaver`.
