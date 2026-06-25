"""Debug fan-out agent for experimenting with parallel tool execution."""
from __future__ import annotations
import asyncio
from typing import TypedDict

from src.codewalk.graph.graph_runtime import GraphRuntime
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.core.fanout import build_fanout_graph
from src.codewalk.core.hitl import compile_with_hitl

class DebugState(TypedDict):
    """State schema for the debug fan-out agent."""
    query: str             # input — read by all parallel nodes
    search_results: str    # written by search_node
    git_log: str           # written by git_node
    blast_radius: str      # written by blast_node
    merged_context: str    # written by merge_node
    answer: str            # written by generate_node

def _make_node(
    store: VectorStore, 
    deps: dict | None, 
    graph_runtime: GraphRuntime | None = None,
    graph_store: GraphStore | None = None):
    """
    Factory: builds async node functions closed over the injected dependencies.
    Same pattern as create_tools() in agent/tools.py — deps injected at
    factory time, NOT pulled from module-level state inside the node.
    """
    async def search_node(state: DebugState) -> dict:
        from src.codewalk.rag.chain import retrieve_corrective
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: retrieve_corrective(state["query"], store, graph_store=graph_store)
        )
        chunks = result["chunks"]
        lines = [
            f"- {chunk['metadata'].get('file_path','?')} "
            f"L{chunk['metadata'].get('start_line','?')}: {chunk['text'][:120]}"
            for chunk in chunks
        ]
        return {"search_results": "\n".join(lines) or "No results found."}
    
    async def git_node(state: DebugState) -> dict:
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-10", "--all", "--", state["query"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        log = stdout.decode().strip() or "No git history found."
        return {"git_log": log}
    
    async def blast_node(state: DebugState) -> dict:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        if not deps and not graph_runtime:
            return {"blast_radius": "No dependency graph available."}
        loop = asyncio.get_event_loop()
        graph_source = graph_runtime if graph_runtime else deps["graph"]
        result = await loop.run_in_executor(
            None,
            lambda: get_blast_radius(state["query"], graph_source)
        )
        summary = (
            f"Risk: {result['risk_level'].upper()} | "
            f"Affected: {result['affected_files']} files | "
            f"Direct: {', '.join(result['direct'][:5]) or 'none'}"
        )
        return {"blast_radius": summary}
    
    return search_node, git_node, blast_node


async def merge_node(state: DebugState) -> dict:
    merged = "\n\n".join([
        f"=== CODE SEARCH ===\n{state.get('search_results', '')}",
        f"=== GIT HISTORY ===\n{state.get('git_log', '')}",
        f"=== BLAST RADIUS ===\n{state.get('blast_radius', '')}",
    ])
    return {"merged_context": merged}

async def generate_node(state: DebugState) -> dict:
    from src.codewalk.config import get_llm
    loop = asyncio.get_event_loop()
    prompt = (
        f"You are a senior engineer. Answer the following question using ONLY "
        f"the context provided. Cite file paths and line numbers.\n\n"
        f"Question: {state['query']}\n\n"
        f"Context:\n{state['merged_context']}"
    )
    llm = get_llm(temperature=0)
    response = await loop.run_in_executor(None, lambda: llm.invoke(prompt))
    return {"answer": response.content}

def create_debug_agent(
    store: VectorStore, deps: dict | None = None, 
    graph_runtime: GraphRuntime | None = None,
    graph_store: GraphStore | None = None):
    """Build the fan-out debug agent.

    Args:
        store:         VectorStore — same instance used by the main chat agent.
        deps:          build_dependency_graph() result — for blast radius.
        graph_runtime: GraphRuntime (igraph) — faster blast radius if available.
        graph_store:   GraphStore (DuckDB) — for corrective RAG graph expansion.

    Called from api/main.py after POST /analyze, alongside create_agent():
        debug_agent = create_debug_agent(store, deps=deps, graph_runtime=runtime, graph_store=graph_store)
    """
    search_node, git_node, blast_node = _make_node(store, deps, graph_runtime, graph_store)

    builder = build_fanout_graph(
        state_type=DebugState,
        parallel_nodes={
            "search": search_node,
            "git":    git_node,
            "blast":  blast_node,
        },
        merge_node=merge_node,
        generate_node=generate_node
    )

    return compile_with_hitl(builder, interrupt_nodes=[])
