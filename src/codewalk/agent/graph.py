import json
import logging
import re
from typing import Annotated, Callable
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from src.codewalk.config import settings, get_llm
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")
from src.codewalk.agent.prompts import AGENT_SYSTEM_PROMPT
from src.codewalk.agent.tools import create_tools, WRITE_TOOL_NAMES
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_runtime import GraphRuntime
from src.codewalk.graph.graph_store import GraphStore

# ─── STATE DEFINITION ────────────────────────────────────────────────
class AgentState(TypedDict):
    """The state that flows through the graph.

    messages: Full conversation history (system + human + AI + tool results).
              The add_messages annotation means new messages get APPENDED.
    """
    messages: Annotated[list, add_messages]


def _last_ai_message_with_tool_calls(messages: list) -> tuple[AIMessage | None, int]:
    """Return the most recent AIMessage with tool_calls and its index."""
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, AIMessage) and msg.tool_calls:
            return msg, i
    return None, -1


def _answered_tool_call_ids(messages: list, after_index: int) -> set[str]:
    return {
        msg.tool_call_id
        for msg in messages[after_index + 1:]
        if isinstance(msg, ToolMessage) and msg.tool_call_id
    }


def _pending_write_tool_calls(messages: list) -> list[dict]:
    """Write tool calls from the latest AI batch that have not been executed yet."""
    last_ai, idx = _last_ai_message_with_tool_calls(messages)
    if not last_ai:
        return []
    answered = _answered_tool_call_ids(messages, idx)
    return [
        tc for tc in last_ai.tool_calls
        if tc.get("name") in WRITE_TOOL_NAMES and tc.get("id") not in answered
    ]


def proposed_write_action(messages: list) -> str:
    """Format pending write tool calls for HITL approval UI."""
    from src.codewalk.agent.tools import format_write_tool_calls
    return format_write_tool_calls(_pending_write_tool_calls(messages))


def make_selective_tool_node(tools: list, allowed_names: frozenset[str]) -> Callable:
    """Run only tool calls whose names are in allowed_names."""
    tool_by_name = {t.name: t for t in tools}

    def node(state: AgentState) -> AgentState:
        messages = state["messages"]
        last_ai, idx = _last_ai_message_with_tool_calls(messages)
        if not last_ai:
            return {"messages": []}

        answered = _answered_tool_call_ids(messages, idx)
        new_messages: list[ToolMessage] = []
        for tc in last_ai.tool_calls:
            name = tc.get("name")
            call_id = tc.get("id", "")
            if name not in allowed_names or call_id in answered:
                continue
            tool = tool_by_name.get(name)
            if not tool:
                new_messages.append(ToolMessage(
                    content=f"Error: unknown tool {name}",
                    tool_call_id=call_id,
                ))
                continue
            try:
                result = tool.invoke(tc.get("args", {}))
                new_messages.append(ToolMessage(
                    content=str(result),
                    tool_call_id=call_id,
                    name=name,
                ))
            except Exception as exc:
                new_messages.append(ToolMessage(
                    content=f"Error: {exc}",
                    tool_call_id=call_id,
                ))
        return {"messages": new_messages}

    return node


# ─── FACTORY FUNCTION ────────────────────────────────────────────────
def create_agent(
    store: VectorStore,
    modules_result: dict,
    files: list[dict] | None = None,
    deps: dict | None = None,
    graph_runtime: GraphRuntime | None = None,
    graph_store: GraphStore | None = None,
    repo_path: str | None = None,
):
    """Build and compile a LangGraph agent with tools and memory.

    Args:
        store: VectorStore with indexed codebase (for search tools).
        modules_result: Output of detect_modules() (for module info tool).
        files: scan_directory() result (for reading order).
        deps: build_dependency_graph() result (for blast radius).
        graph_runtime: Optional GraphRuntime for igraph fast path.
        repo_path: Root of the repository the agent operates on.

    Returns:
        Compiled StateGraph — call it with .invoke() or .stream().
    """
    _log("[agent] Creating agent with tools...")
    # ── Step 1: Create tools ─────────────────────────────────────
    tools = create_tools(
        store, modules_result, files=files, deps=deps,
        graph_runtime=graph_runtime, graph_store=graph_store,
        repo_path=repo_path,
    )
    read_tool_names = frozenset(t.name for t in tools) - WRITE_TOOL_NAMES

    # ── Step 2: Create LLM with tools bound ──────────────────────
    llm = get_llm(temperature=0, reasoning=False)
    llm_with_tools = llm.bind_tools(tools)

    # ── Step 3: Define the agent node ────────────────────────────
    # Regex to find {"name": "tool_name", "arguments": {...}} in text.
    # Uses recursive brace matching to handle nested JSON in arguments.
    _TOOL_CALL_RE = re.compile(
        r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})\s*\}',
        re.DOTALL,
    )

    def agent_node(state: AgentState) -> AgentState:
        """Call the LLM with the conversation history + system prompt."""
        messages = [SystemMessage(content=AGENT_SYSTEM_PROMPT)] + state["messages"]
        response = llm_with_tools.invoke(messages)

        # Fallback: some models output tool calls as JSON text
        # instead of structured tool_calls. Detect and convert.
        if not response.tool_calls and response.content:
            match = _TOOL_CALL_RE.search(response.content)
            if match:
                tool_name = match.group(1)
                # Validate it's one of our tools
                tool_names = {t.name for t in tools}
                if tool_name in tool_names:
                    try:
                        tool_args = json.loads(match.group(2))
                    except json.JSONDecodeError:
                        tool_args = {}
                    _log(f"[agent] Fallback: parsed text tool call → {tool_name}({tool_args})")
                    response = AIMessage(
                        content="",
                        tool_calls=[{"name": tool_name, "args": tool_args, "id": f"call_{tool_name}"}],
                    )

        return {"messages": [response]}

    # ── Step 4: Define routing functions ─────────────────────────
    def route_after_agent(state: AgentState):
        """Route read tools immediately; pause before write tools (apply_fix)."""
        messages = state.get("messages", [])
        if not messages:
            return END
        last_message = messages[-1]
        if not getattr(last_message, "tool_calls", None):
            return END
        names = {tc["name"] for tc in last_message.tool_calls}
        if names & WRITE_TOOL_NAMES:
            if names - WRITE_TOOL_NAMES:
                return "read_tools"
            return "write_tools"
        return "read_tools"

    def route_after_read(state: AgentState):
        """After read tools, interrupt if apply_fix is still pending."""
        if _pending_write_tool_calls(state.get("messages", [])):
            return "write_tools"
        return "agent"

    # ── Step 5: Build the graph ──────────────────────────────────
    graph = StateGraph(AgentState)

    graph.add_node("agent", agent_node)
    graph.add_node("read_tools", make_selective_tool_node(tools, read_tool_names))
    graph.add_node("write_tools", make_selective_tool_node(tools, WRITE_TOOL_NAMES))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"read_tools": "read_tools", "write_tools": "write_tools", END: END},
    )
    graph.add_conditional_edges(
        "read_tools",
        route_after_read,
        {"write_tools": "write_tools", "agent": "agent"},
    )
    graph.add_edge("write_tools", "agent")

    # ── Step 6: Compile with memory ──────────────────────────────
    from src.codewalk.core.hitl import compile_with_hitl
    return compile_with_hitl(graph, interrupt_nodes=["write_tools"])
