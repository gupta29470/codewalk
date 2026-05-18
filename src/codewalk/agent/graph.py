"""
=============================================================================
 graph.py - LangGraph Agent Construction
=============================================================================

WHAT THIS FILE DOES:
    Builds a LangGraph StateGraph (the "agent") that can:
    1. Receive a user message
    2. Decide if it needs to call a tool
    3. Call the tool and get results
    4. Either call another tool or give a final answer

    The graph has 2 nodes: "agent" (LLM) and "tools" (executor).
    It loops between them until the LLM gives a final answer.

HOW THE GRAPH WORKS:
    START -> agent -> [has tool_calls?] -> tools -> agent -> ... -> END
                   -> [no tool_calls?] -> END

WHERE IT'S CALLED:
    - state.py -> initialize() creates the agent after indexing
    - api/main.py -> /chat endpoint invokes it

DEPENDENCIES:
    - tools.py: create_tools() for the 8 agent tools
    - prompts.py: system prompt
    - config.py: get_llm()
    - langgraph: StateGraph, ToolNode, MemorySaver

=============================================================================
"""

import json
import logging
import re
from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import AIMessage, SystemMessage

from src.codewalk.config import settings, get_llm
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")
from src.codewalk.agent.prompts import AGENT_SYSTEM_PROMPT
from src.codewalk.agent.tools import create_tools
from src.codewalk.embeddings.vector_store import VectorStore


# =============================================================================
# State Definition
# =============================================================================

class AgentState(TypedDict):
    """The state flowing through the graph.

    messages: Full conversation history. The add_messages annotation
    means new messages get APPENDED (not replaced).
    """
    messages: Annotated[list, add_messages]


# =============================================================================
# create_agent() - Factory Function
# =============================================================================

def create_agent(store: VectorStore, modules_result: dict, files: list[dict] = None, deps: dict = None):
    """Build and compile a LangGraph agent with tools and memory.

    Args:
        store: VectorStore with indexed codebase
        modules_result: Output of detect_modules()
        files: scan_directory() result (for reading order tool)
        deps: build_dependency_graph() result (for blast radius tool)

    Returns:
        Compiled StateGraph - call with .invoke() or .stream()
    """
    _log("[agent] Creating agent with tools...")

    # 1. Create the 8 tools
    tools = create_tools(store, modules_result, files=files, deps=deps)

    # 2. Bind tools to LLM (so it knows what's available)
    llm = get_llm(temperature=0, reasoning=False)
    llm_with_tools = llm.bind_tools(tools)

    # 3. Regex for fallback tool-call detection
    # Some models output tool calls as JSON text instead of structured
    _TOOL_CALL_RE = re.compile(
        r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^{}]*\})\s*\}',
        re.DOTALL,
    )

    def agent_node(state: AgentState) -> AgentState:
        """Call LLM with conversation history + system prompt."""
        messages = [SystemMessage(content=AGENT_SYSTEM_PROMPT)] + state["messages"]
        response = llm_with_tools.invoke(messages)

        # Fallback: detect JSON tool calls in text response
        if not response.tool_calls and response.content:
            match = _TOOL_CALL_RE.search(response.content)
            if match:
                tool_name = match.group(1)
                tool_names = {t.name for t in tools}
                if tool_name in tool_names:
                    try:
                        tool_args = json.loads(match.group(2))
                    except json.JSONDecodeError:
                        tool_args = {}
                    _log(f"[agent] Fallback: parsed text tool call -> {tool_name}({tool_args})")
                    response = AIMessage(
                        content="",
                        tool_calls=[{"name": tool_name, "args": tool_args, "id": f"call_{tool_name}"}],
                    )

        return {"messages": [response]}

    def should_continue(state: AgentState):
        """Route: tool_calls -> "tools" node, else -> END."""
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return "tools"
        return END

    # 4. Build the graph
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))

    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    # 5. Compile with memory (conversation persistence)
    memory = MemorySaver()
    return graph.compile(checkpointer=memory)