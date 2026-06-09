from __future__ import annotations
from dataclasses import dataclass, field
import asyncio

from src.codewalk.research.planner import SubQuestion
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore

RESEARCHER_PROMPT = """You are a codebase researcher. Answer the sub-question below
using ONLY the code chunks provided. Be specific — cite file paths and line numbers.
If the chunks don't answer the question, say so clearly.

Sub-question: {sub_question}

Code chunks:
{chunks}"""

@dataclass
class SubFindings:
    sub_question_id: str
    sub_question_text: str
    findings: str           # structured answer with citations
    sources: list[str] = field(default_factory=list)  # file paths referenced

def make_researcher(
    sub_question: SubQuestion,
    store: VectorStore,
    graph_store: GraphStore | None = None,
) -> callable:
    """Return an async node function for this sub-question.
    Closes over sub_question + store + graph_store — injected at factory time (same pattern as V2.6).

    Uses retrieve_corrective() — the full corrective RAG pipeline:
      L1: distance filter → L2: retrieval quality check → L2b: graph expansion → L3: keyword grading
    Same pipeline as codewalk_search_codebase — NOT raw store.search().

    The returned function matches LangGraph node signature: (state) → dict.
    """
    from src.codewalk.config import get_llm
    from src.codewalk.rag.chain import retrieve_corrective

    async def research_node(state: dict) -> dict:
        loop = asyncio.get_running_loop()

        # 1. Full corrective RAG search — same pipeline as codewalk_search_codebase
        result = await loop.run_in_executor(
            None,
            lambda: retrieve_corrective(
                sub_question.text, store, graph_store=graph_store
            )
        )
        chunks = result["chunks"]

        # 2. Format chunks with citations
        formatted = "\n\n".join(
            f"[{chunk['metadata'].get('file_path', '?')} L{chunk['metadata'].get('start_line', '?')}]\n{chunk['text'][:500]}"
            for chunk in chunks
        )

        sources = list({chunk["metadata"].get("file_path", "") for chunk in chunks if chunk["metadata"].get("file_path")})

        # 3. LLM answers the sub-question from chunks
        llm = get_llm(temperature=0)
        prompt = RESEARCHER_PROMPT.format(sub_question=sub_question.text, chunks=formatted or "No relevant code found.")
        response = await loop.run_in_executor(None, lambda: llm.invoke(prompt))

        findings = SubFindings(
            sub_question_id=sub_question.id,
            sub_question_text=sub_question.text,
            findings=response.content.strip(),
            sources=sources,
        )

        # Each node appends to shared 'results' list via Annotated[list, operator.add]
        return {"results": [findings]}
    
    return research_node
