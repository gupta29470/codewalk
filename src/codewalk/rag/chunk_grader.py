from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log

KEYWORD_OVERLAP_THRESHOLD = 0.2
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
     "Examples:\n"
     "- Question: 'How does scan_directory work?' → chunk containing the "
     "  `scan_directory` function definition is RELEVANT.\n"
     "- Question: 'How does authentication work?' → chunk containing a "
     "  logging helper from auth.py is IRRELEVANT (wrong function).\n\n"
     "Return a grade for every chunk — do not skip any. If a chunk is missing "
     "from your output, it will be treated as irrelevant."),
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
        symbol_type = meta.get("symbol_type", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)

        label = f"[Chunk {i}] {file_path}"
        if symbol:
            label += f" | {symbol_type}: {symbol}" if symbol_type else f" | {symbol}"
        if start_line:
            label += f" (lines {start_line}-{end_line})"
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
    graded_indices = {g.index for g in grade_result.grades}
    relevant_indices = {g.index for g in grade_result.grades if g.relevant}

    # Safety: if the LLM omitted any indices, keep those chunks rather than dropping them.
    missing_indices = set(range(len(results))) - graded_indices
    if missing_indices:
        _log(f"[chunk_grader] {len(missing_indices)} chunk(s) missing grades; keeping them")
        relevant_indices |= missing_indices

    filtered = [r for i, r in enumerate(results) if i in relevant_indices]

    _log(f"[chunk_grader] {len(filtered)}/{len(results)} chunks graded relevant")
    return filtered


# ── Stopwords for free keyword grading ───────────────────────────────
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

# sklearn misses a few common words — supplement them
_STOPWORDS = ENGLISH_STOP_WORDS.union({"does", "did", "need", "shall"})


def _tokenize(text: str) -> set[str]:
    """Extract keyword tokens, splitting compound identifiers.

    'get_blast_radius' → {'get_blast_radius', 'get', 'blast', 'radius'}
    'filterByDistance' → {'filterbydistance', 'filter', 'by', 'distance'}
    """
    import re
    # Match word-like tokens (including underscored identifiers)
    raw = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", text))
    tokens = set()
    for word in raw:
        lower = word.lower()
        tokens.add(lower)
        # Split on underscores: get_blast_radius → get, blast, radius
        if "_" in lower:
            tokens.update(lower.split("_"))
        # Split camelCase: filterByDistance → filter, by, distance
        parts = re.findall(r"[a-z]+|[A-Z][a-z]*", word)
        if len(parts) > 1:
            tokens.update(p.lower() for p in parts)
    # Remove stopwords and short tokens
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}


def grade_chunks_free(question: str, results: list[dict]) -> list[dict]:
    """Grade chunks by keyword overlap — zero LLM cost.

    Tokenizes the question into keywords, scores each chunk by how many
    question keywords appear in the chunk text, and keeps chunks above
    a minimum overlap threshold.

    If nothing passes the threshold, returns all chunks (same fallback
    behavior as the LLM grader).

    Args:
        question: The user's original question.
        results: Search results from store.search().

    Returns:
        Filtered list — only chunks with sufficient keyword overlap.
    """
    if not results:
        return []

    q_tokens = _tokenize(question)
    if not q_tokens:
        return results  # can't grade without keywords

    scored = []
    for result in results:
        chunk_tokens = _tokenize(result["text"])
        if not chunk_tokens:
            scored.append((0.0, result))
            continue
        overlap = len(q_tokens & chunk_tokens)
        score = overlap / len(q_tokens)
        scored.append((score, result))

    # Keep chunks with at least 20% keyword overlap
    threshold = KEYWORD_OVERLAP_THRESHOLD
    filtered = [r for score, r in scored if score >= threshold]

    if not filtered:
        # Nothing passed — keep all (same as LLM grader fallback)
        _log(f"[chunk_grader_free] No chunks passed threshold, keeping all {len(results)}")
        return results

    _log(f"[chunk_grader_free] {len(filtered)}/{len(results)} chunks passed keyword filter")
    return filtered
