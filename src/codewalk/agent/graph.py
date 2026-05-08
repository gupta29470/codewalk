from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import SystemMessage

from src.codewalk.config import settings, get_llm
from src.codewalk.agent.prompts import AGENT_SYSTEM_PROMPT
from src.codewalk.agent.tools import create_tools
from src.codewalk.embeddings.vector_store import VectorStore

# ─── STATE DEFINITION ────────────────────────────────────────────────
class AgentState(TypedDict):
    """The state that flows through the graph.

    messages: Full conversation history (system + human + AI + tool results).
              The add_messages annotation means new messages get APPENDED.
    """
    messages: Annotated[list, add_messages]


# ─── FACTORY FUNCTION ────────────────────────────────────────────────
def create_agent(store: VectorStore, modules_result: dict):
    """Build and compile a LangGraph agent with tools and memory.

    Args:
        store: VectorStore with indexed codebase (for search tools).
        modules_result: Output of detect_modules() (for module info tool).

    Returns:
        Compiled StateGraph — call it with .invoke() or .stream().
    """
    # ── Step 1: Create tools ─────────────────────────────────────
    tools = create_tools(store, modules_result)

    # ── Step 2: Create LLM with tools bound ──────────────────────
    llm = get_llm(temperature=0, reasoning=False)
    llm_with_tools = llm.bind_tools(tools)

    # ── Step 3: Define the agent node ────────────────────────────
    def agent_node(state: AgentState) -> AgentState:
        """Call the LLM with the conversation history + system prompt."""
        messages = [SystemMessage(content=AGENT_SYSTEM_PROMPT)] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}
    
    # ── Step 4: Define the routing function ──────────────────────
    def should_continue(state: AgentState):
        """Decide: did the LLM want to call a tool, or give a final answer?"""
        last_message = state["messages"][-1]
        if last_message.tool_calls:
            return "tools"
        return END
    
    # ── Step 5: Build the graph ──────────────────────────────────
    graph = StateGraph(AgentState)

    # Add nodes (the boxes in the flowchart)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))

    # Add edges (the arrows between boxes)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END}
    )
    graph.add_edge("tools", "agent")     

    # ── Step 6: Compile with memory ──────────────────────────────
    memory = MemorySaver()
    return graph.compile(checkpointer=memory)

    