"""LLM-as-reranker for retrieved code chunks.

Scores each chunk 0-10 for relevance to the query, then keeps the top-k.
This is slower than the free keyword grader but more accurate for complex
questions where keyword overlap is misleading.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log


class ChunkScore(BaseModel):
    """Relevance score for a single chunk."""

    index: int = Field(description="The chunk index (0-based)")
    score: int = Field(
        ge=0,
        le=10,
        description=(
            "Relevance score: 10 = essential for answering the question, "
            "0 = completely unrelated."
        ),
    )
    reason: str = Field(default="", description="One-line reason for the score")


class RerankResult(BaseModel):
    """Batch relevance scores for reranking."""

    scores: list[ChunkScore] = Field(description="One score per chunk")


_RERANK_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a relevance judge for a code retrieval system.\n"
     "Given a question and a list of retrieved code chunks, score EACH chunk\n"
     "from 0 to 10 based on how useful it is for answering the question.\n\n"
     "Scoring guide:\n"
     "- 10: directly contains the answer (e.g., the function definition asked about)\n"
     "- 7-9: highly relevant context (callers, related logic)\n"
     "- 4-6: somewhat related but not essential\n"
     "- 1-3: barely related\n"
     "- 0: unrelated\n\n"
     "Return a score for every chunk. Do not skip any."),
    ("human",
     "Question: {question}\n\n"
     "Retrieved chunks:\n{chunks}\n\n"
     "Score each chunk."),
])


def rerank_chunks(question: str, results: list[dict], top_k: int | None = None) -> list[dict]:
    """Rerank retrieved chunks using an LLM and return the top-k.

    Args:
        question: The user's original question.
        results: Search results from store.search().
        top_k: If provided, keep only this many chunks after reranking.

    Returns:
        Chunks sorted by relevance score (highest first), optionally truncated.
    """
    if not results:
        return []

    chunk_texts = []
    for i, result in enumerate(results):
        meta = result["metadata"]
        file_path = meta.get("file_path", "?")
        symbol = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)

        label = f"[Chunk {i}] {file_path}"
        if symbol:
            label += f" | {symbol_type}: {symbol}" if symbol_type else f" | {symbol}"
        if start_line:
            label += f" (lines {start_line}-{end_line})"
        text = result["text"][:1200]
        chunk_texts.append(f"{label}\n{text}")

    chunks_str = "\n\n".join(chunk_texts)

    try:
        llm = get_llm(temperature=0, reasoning=False)
        reranker = _RERANK_PROMPT | llm.with_structured_output(RerankResult)
        result: RerankResult = reranker.invoke({
            "question": question,
            "chunks": chunks_str,
        })
    except Exception as e:
        _log(f"[reranker] LLM rerank failed: {e}; returning original order")
        return results

    scored_indices = {s.index for s in result.scores}
    missing = set(range(len(results))) - scored_indices
    if missing:
        _log(f"[reranker] {len(missing)} chunk(s) missing scores; keeping original scores")

    score_map = {s.index: s.score for s in result.scores}
    # Missing chunks get a neutral score of 5 so they survive but rank lower.
    scored = [(score_map.get(i, 5), r) for i, r in enumerate(results)]
    scored.sort(key=lambda x: x[0], reverse=True)

    reranked = [r for _, r in scored]
    if top_k:
        reranked = reranked[:top_k]

    _log(f"[reranker] reranked {len(results)} chunks, returning {len(reranked)}")
    return reranked
