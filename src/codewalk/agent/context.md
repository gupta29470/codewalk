# `src/codewalk/agent/` — Conversational Coding Agent

This package implements a LangGraph agent that can answer questions about the codebase and propose/apply code changes with human approval.

## Modules

| File | Role |
|------|------|
| `graph.py` | `create_agent()` — compiles the LangGraph state graph with tools, memory (SQLite checkpointer), and interrupt logic for human-in-the-loop. |
| `tools.py` | 13 agent tool definitions: search, explain function, module info, overview, blast radius, reading order, execution flow, architecture health, review diff/file, apply fix, verify fix, load guidelines. |
| `core/hitl.py` | Shared human-in-the-loop interrupt helpers; `proposed_write_action()` extracts a pending file edit from agent messages. |

## Data flow

```
user message + thread_id
    ↓
agent graph (tools + LLM)
    ↓
if write tool → interrupt + propose action
    ↓
POST /chat/approve or MCP codewalk_approve_action resumes/rejects
    ↓
apply_fix tool writes file
```

## Connections

- `graph.py` is built by `api/state.py` after analysis and cached in `state._agent`.
- `tools.py` imports from `rag/`, `query/`, `review/`, `embeddings/`, `graph/`, and `doc_knowledge/`.
- Used by API `/chat` and `/chat/approve`. The tools defined here mirror the capabilities exposed by the standalone MCP tools, but the agent composes them itself.
- `core/hitl.py` provides shared HITL utilities (there is no `agent/hitl.py`).

## Recent fixes

- `graph.py` compiles agents via `core/hitl.compile_with_hitl()`, which now wraps the compiled graph in `_ThreadSafeGraph` to serialize access to the SQLite checkpointer across API worker threads.
- `/chat/approve` now preserves `HTTPException` statuses and maps `RuntimeError` to 400 instead of swallowing everything as 500.
