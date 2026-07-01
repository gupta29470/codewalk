"""Deep research fan-out orchestrator: plan, search, synthesize, reflect."""
from __future__ import annotations
import asyncio
from typing import TypedDict, Any, Annotated
import operator

from src.codewalk.research.planner import decompose, SubQuestion
from src.codewalk.research.researcher import make_researcher, SubFindings
from src.codewalk.research.synthesizer import (
    merge_findings, make_synthesizer, StructuredReport,
    SYNTHESIS_CRITIC_PROMPT,
)
from src.codewalk.core.fanout import build_fanout_graph
from src.codewalk.core.hitl import compile_with_hitl
from src.codewalk.core.reflect import reflect
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore

class ResearchState(TypedDict):
    """LangGraph state for deep research."""
    question: str
    results: Annotated[list, operator.add]  # all researchers append SubFindings here
    merged_findings: list
    report: Any   # StructuredReport


def _improve_report(report: StructuredReport, critique: str) -> StructuredReport:
    """Apply critic feedback to improve the report. Passed to reflect()."""
    from src.codewalk.config import get_llm
    if "LGTM" in critique:
        return report
    
    llm = get_llm(temperature=0)
    prompt = (
        f"Improve this research report based on the critique below.\n\n"
        f"CRITIQUE:\n{critique}\n\n"
        f"ORIGINAL REPORT:\n{report.markdown}"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior engineer improving a codebase research report.\n\n"
                "Rules:\n"
                "- Keep all existing file path citations — do not remove them.\n"
                "- Fix issues raised in the critique (missing files, uncited claims, gaps).\n"
                "- Do not remove valid findings from the original report.\n"
                "- Maintain the markdown structure (headers, code blocks, bullet points).\n"
                "- If the critique mentions missing context, add a note rather than inventing code."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    response = llm.invoke(messages)
    return StructuredReport(
        question=report.question,
        markdown=response.content.strip(),
        sources=report.sources,
    )


async def _run_research_pipeline(
    question: str,
    sub_questions: list[SubQuestion],
    store: VectorStore,
    graph_store: GraphStore | None,
    interrupt_before_research: bool,
) -> StructuredReport:
    """Async research execution: build graph, compile with async checkpointer, run."""
    parallel_nodes = {
        sub_question.id: make_researcher(sub_question, store, graph_store)
        for sub_question in sub_questions
    }

    builder = build_fanout_graph(
        state_type=ResearchState,
        parallel_nodes=parallel_nodes,
        merge_node=merge_findings,
        generate_node=make_synthesizer(graph_store),
    )

    interrupt_nodes = [list(parallel_nodes.keys())[0]] if interrupt_before_research else []
    graph_ctx = compile_with_hitl(
        builder,
        interrupt_nodes=interrupt_nodes,
        async_checkpointer=True,
    )

    initial_state = {
        "question": question,
        "results": [],
        "merged_findings": [],
        "report": None,
    }

    async with graph_ctx as graph:
        result = await graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": f"research-{hash(question)}"}},
        )

    return result["report"]


def deep_research(question: str,
    store: VectorStore,
    graph_store: GraphStore | None = None,
    depth: str = "standard",
    interrupt_before_research: bool = False,) -> StructuredReport:
    """Run the full deep research pipeline.

    Args:
        question:                 The complex research question.
        store:                    VectorStore — injected, same as chat agent.
        graph_store:              GraphStore (DuckDB) — for corrective RAG graph expansion.
        depth:                    "quick" | "standard" | "deep"
        interrupt_before_research: If True, hitl gate fires before fan-out starts.

    Returns:
        StructuredReport with markdown, citations, optional diagram.
    """
    # Step 1: Decompose question → sub-questions
    sub_questions: list[SubQuestion] = decompose(question, depth)

    # Step 2-5: Build and run the async research graph
    draft_report: StructuredReport = asyncio.run(
        _run_research_pipeline(
            question,
            sub_questions,
            store,
            graph_store,
            interrupt_before_research,
        )
    )

    # Step 6: Reflect — critic pass on the synthesized report
    improved_report = reflect(
        initial_output=draft_report,
        context=question,
        critic_system_prompt=SYNTHESIS_CRITIC_PROMPT,
        improve_fn=_improve_report,
        iterations=1,
    )

    return improved_report
