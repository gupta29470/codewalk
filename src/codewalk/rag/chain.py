import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import settings, get_llm
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT
from src.codewalk.log import log as _log
from src.codewalk.rag.retrieval_quality import filter_by_distance, is_retreival_good
from src.codewalk.rag.answer_grader import grade_answer
from src.codewalk.rag.query_rewriter import rewrite_query

logger = logging.getLogger("codewalk")

MAX_RETRIES = 5

def format_context(results: list[dict]) -> str:
    """Turn search results into a rich context string for the prompt.

    Uses metadata to show file path, function/class name, and line numbers
    so the LLM can reference them in its answer.
    """
    parts = []

    for result in results:
        meta = result["metadata"]
        file_path = meta["file_path"]
        symbol_name = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)

        # Build a descriptive header
        header = f"--- {file_path}"
        if symbol_name:
            header += f" | {symbol_type}: {symbol_name}"
        if start_line:
            header += f" (lines {start_line}-{end_line})"
        header += " ---"

        parts.append(f"{header}\n{result['text']}")

    return "\n\n".join(parts)

def ask(question: str, store: VectorStore, n_results: int = 5) -> str:
    _log(f"[rag] Question: {question[:80]}...")
    """Full RAG pipeline: question → retrieve → prompt → LLM → answer."""
    # 1. Retrieve relevant chunks from ChromaDB
    results = store.search(question, n_results=n_results)

    # 2. Format chunks into context string
    context = format_context(results)

    # 3. Build the prompt
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", QUESTION_PROMPT)
        ]
    )

    # 4. Create the chain: prompt → LLM → parse output as string
    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    # 5. Run it
    answer = chain.invoke({
        "context": context,
        "question": question,
    })

    return answer


def ask_corrective(question: str, store, n_results: int = 5) -> dict:
    """Corrective RAG: distance filter → generate → grade answer → retry if bad.

    Layer 1 (FREE):  filter_by_distance() drops noise chunks
    Layer 2 (FREE):  retrieval_is_good() decides if we need to rewrite
    Layer 3 (1 LLM): grade_answer() checks faithfulness + relevance

    Returns:
        {
            "answer": str,            — the final answer text
            "confident": bool,        — True if all quality checks passed
            "retries": int,           — how many retries were needed (0 = first try worked)
            "retrieval_confidence": float, — 0.0-1.0 from distance scoring
            "relevant_chunks": int,   — how many chunks survived filtering
        }
    """
    from langchain_core.output_parsers import StrOutputParser
    from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT

    llm = get_llm(temperature=0, reasoning=False)
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", QUESTION_PROMPT)
    ])

    gen_chain = prompt | llm | StrOutputParser()

    current_question = question
    best_answer = None
    best_confidence = 0.0

    for attempt in range(MAX_RETRIES):
        _log(f"[corrective] Attempt {attempt + 1}/{1 + MAX_RETRIES}: '{current_question[:60]}'")

        # ── Layer 1 (FREE): Retrieve + distance filter ──
        results = store.search(current_question, n_results=n_results)
        if not results:
            if attempt < MAX_RETRIES:
                current_question = rewrite_query(current_question)
                continue
            break

        filtered, confidence = filter_by_distance(results)

        # ── Layer 2 (FREE): Check if retrieval is good enough ──
        if not is_retreival_good(confidence, len(filtered)):
            _log(f"[corrective] Retrieval too weak (confidence={confidence:.2f}) — rewriting")
            if attempt < MAX_RETRIES:
                current_question = rewrite_query(current_question)
                continue

            # Last attempt — use whatever we have
            if not filtered:
                filtered = results
            
        # ── Generate answer ──
        context = format_context(filtered)
        answer = gen_chain.invoke({
            "context": context,
            "question": question,  # always ORIGINAL question for generation
        })

        # ── Layer 3 (1 LLM call): Grade the answer ──
        grade = grade_answer(question, context, answer)

        if grade.faithful and grade.relevant:
            _log(f"[corrective] Answer GOOD on attempt {attempt + 1}")
            return {
                "answer": answer,
                "confident": True,
                "retries": attempt,
                "retrieval_confidence": confidence,
                "relevant_chunks": len(filtered),
            }
        
        # Answer graded bad — save best effort, rewrite and retry
        best_answer = answer
        best_confidence = confidence

        _log(f"[corrective] Answer BAD ({grade.reason[:60]}) — rewriting")

        if attempt < MAX_RETRIES:
            current_question = rewrite_query(current_question)

    # ── Exhausted retries ──
    _log("[corrective] Retries exhausted — returning best-effort answer")
    return {
        "answer": best_answer or "I couldn't find relevant information to answer that question.",
        "confident": False,
        "retries": MAX_RETRIES,
        "retrieval_confidence": best_confidence,
        "relevant_chunks": 0,
    }

