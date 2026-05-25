from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log


class ChunkRelevance(BaseModel):
    """LLM verdict on whether a single chunk is relevant to the query."""
    index: int = Field(description="The chunk index (0-based)")
    relevant: bool = Field(description="True if this chunk helps answer the question")


class ChunkGradeResult(BaseModel):
    """Batch result: which chunks are relevant to the user's question."""
    grades: list[ChunkRelevance] = Field(description="One grade per chunk")


_CHUNK_GRADER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a relevance grader for a code search engine.\n"
     "Given a user's question and a list of retrieved code chunks, "
     "grade EACH chunk: is it relevant to answering the question?\n\n"
     "A chunk is relevant if it contains code, definitions, or logic "
     "that directly helps answer the question.\n"
     "A chunk is irrelevant if it's from the right file but wrong function, "
     "or from a completely unrelated part of the codebase.\n\n"
     "Return a grade for every chunk — do not skip any."),
    ("human",
     "Question: {question}\n\n"
     "Retrieved chunks:\n{chunks}\n\n"
     "Grade each chunk."),
])


def grade_chunks(question: str, results: list[dict]) -> list[dict]:
    """Grade each retrieved chunk for relevance to the question.

    Uses a single batched LLM call to grade all chunks at once.
    Returns only the chunks that the LLM considers relevant.

    Cost: 1 LLM call regardless of chunk count.

    Args:
        question: The user's original question.
        results: Search results from store.search(). Each has "text" and "metadata".

    Returns:
        Filtered list — only chunks graded as relevant.
    """
    if not results:
        return []

    # Format chunks for the prompt
    chunk_texts = []
    for i, result in enumerate(results):
        meta = result["metadata"]
        file_path = meta.get("file_path", "?")
        symbol = meta.get("symbol_name", "")
        label = f"[Chunk {i}] {file_path}"
        if symbol:
            label += f" :: {symbol}"
        # Truncate to avoid prompt overflow
        text = result["text"][:1500]
        chunk_texts.append(f"{label}\n{text}")

    chunks_str = "\n\n".join(chunk_texts)

    llm = get_llm(temperature=0, reasoning=False)
    grader = _CHUNK_GRADER_PROMPT | llm.with_structured_output(ChunkGradeResult)

    grade_result: ChunkGradeResult = grader.invoke({
        "question": question,
        "chunks": chunks_str,
    })

    # Filter to only relevant chunks
    relevant_indices = {g.index for g in grade_result.grades if g.relevant}
    filtered = [r for i, r in enumerate(results) if i in relevant_indices]

    _log(f"[chunk_grader] {len(filtered)}/{len(results)} chunks graded relevant")
    return filtered
