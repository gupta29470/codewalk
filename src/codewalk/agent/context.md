# `src/codewalk/agent/` — Conversational Coding Agent

This package implements a LangGraph agent that can answer questions about the codebase and propose/apply code changes with human approval. It is used by the API flow only; MCP does not use this agent — it exposes equivalent tools directly to the host IDE.

## Modules

| File | Role |
|------|------|
| `graph.py` | `create_agent()` — compiles the LangGraph state graph with tools, memory (SQLite/AsyncSqliteSaver checkpointer via `core/hitl`), and interrupt logic for human-in-the-loop. Also exposes `proposed_write_action()` to extract pending file edits from agent messages. |
| `tools.py` | Agent tool definitions: search codebase (multi-query), explain function, module info, overview, blast radius, reading order, execution flow, architecture health, review diff/file, apply fix, verify fix, run static analysis, run tests, load guidelines. |
| `prompts.py` | System prompts and prompt templates for the agent. |

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
- `tools.py` imports from `rag/`, `query/`, `review/`, `embeddings/`, `graph/`, `tools/`, and `doc_knowledge/`.
- Used by API `/chat` and `/chat/approve`. The tools defined here mirror capabilities exposed by standalone MCP tools, but the agent composes them itself.
- Human-in-the-loop compilation is provided by `core/hitl.py` (`compile_with_hitl()`); there is no `agent/hitl.py`.

## Notes

- `graph.py` compiles agents via `core/hitl.compile_with_hitl()`, which wraps the compiled graph in `_ThreadSafeGraph` to serialize access to the SQLite checkpointer across API worker threads. The async checkpointer variant is used by `research/deep_research.py`.
- `search_codebase` in `tools.py` expands every user query into 1–3 complementary search angles via `expand_query()`, runs corrective RAG in parallel, and synthesizes the results into a single answer.
- Per `AGENTS.md` / architecture rules: the agent lives in the API flow and may call `get_llm()`. MCP tools do not use this agent.
