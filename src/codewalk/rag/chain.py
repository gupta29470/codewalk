"""Corrective RAG chain: retrieve, grade, generate, and verify answers over the codebase."""
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
from src.codewalk.rag.chunk_grader import grade_chunks
from src.codewalk.rag.graph_expansion import expand_via_graph
from src.codewalk.rag.query_expander import expand_query
from src.codewalk.rag.symbol_lookup import lookup_symbol
from src.codewalk.rag.reranker import rerank_chunks

logger = logging.getLogger("codewalk")

MAX_RETRIES = 5


def _dynamic_n_results(question: str, base: int = 5) -> int:
    """Choose retrieval depth based on query complexity."""
    lowered = question.lower()
    overview_indicators = ["overview", "summary", "architecture", "how does", "explain", "flow"]
    if any(ind in lowered for ind in overview_indicators):
        return max(base, 12)
    return base


def _deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    """Deduplicate chunks by a stable key built from file + symbol + line range."""
    seen = set()
    unique = []
    for chunk in chunks:
        meta = chunk.get("metadata", {})
        key = (
            meta.get("file_path", ""),
            meta.get("symbol_name", ""),
            meta.get("start_line", 0),
            meta.get("end_line", 0),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


def _retrieve_with_fallbacks(
    question: str,
    store,
    graph_store=None,
    n_results: int = 5,
    use_expansion: bool = True,
    use_reranker: bool = False,
) -> tuple[list[dict], float, bool]:
    """Retrieve chunks with symbol lookup, expansion, and reranking.

    Returns:
        (chunks, confidence, retrieval_good)
    """
    from src.codewalk.rag.chunk_grader import grade_chunks_free

    n_results = _dynamic_n_results(question, n_results)
    all_chunks: list[dict] = []

    # ── Layer 0: Deterministic symbol lookup ──
    symbol_chunks = lookup_symbol(graph_store, store, question)
    if symbol_chunks:
        all_chunks.extend(symbol_chunks)
        _log(f"[retrieve] symbol lookup added {len(symbol_chunks)} chunks")

    # ── Layer 1: Semantic search ──
    semantic_results = store.search(question, n_results=n_results)
    if semantic_results:
        all_chunks.extend(semantic_results)

    if not all_chunks:
        return [], 0.0, False

    all_chunks = _deduplicate_chunks(all_chunks)
    total_retrieved = len(all_chunks)
    filtered, confidence = filter_by_distance(all_chunks)

    # ── Layer 2: Retrieval quality check ──
    retrieval_good = is_retreival_good(confidence, len(filtered))

    # ── Layer 2a: Query expansion + multi-query retrieval ──
    if use_expansion and not retrieval_good:
        try:
            expanded = expand_query(question)
            extra_queries = expanded.queries[1:] if len(getattr(expanded, "queries", [])) > 1 else []
            for q in extra_queries:  # skip original; already searched
                extra = store.search(q, n_results=n_results)
                filtered.extend(extra)
            filtered = _deduplicate_chunks(filtered)
            if expanded.symbol_hint:
                hint_chunks = lookup_symbol(
                    graph_store, store, expanded.symbol_hint, include_callers=False
                )
                if hint_chunks:
                    filtered = _deduplicate_chunks(hint_chunks + filtered)
            # Recompute distance filter after expansion
            filtered, confidence = filter_by_distance(filtered)
            retrieval_good = is_retreival_good(confidence, len(filtered))
            _log(f"[retrieve] query expansion → {len(filtered)} chunks")
        except Exception as e:
            _log(f"[retrieve] query expansion failed: {e}")

    # ── Layer 2b: Graph expansion fallback ──
    if not retrieval_good and graph_store and filtered:
        expanded = expand_via_graph(filtered, store, question, graph_store)
        if len(expanded) > len(filtered):
            filtered = expanded
            confidence = max(confidence, 0.35)
            retrieval_good = is_retreival_good(confidence, len(filtered))
            _log(f"[retrieve] graph expansion recovered {len(expanded)} chunks")

    if not filtered:
        filtered = all_chunks

    # ── Layer 3: Keyword-based chunk grading ──
    graded = grade_chunks_free(question, filtered)
    if graded:
        filtered = graded

    # ── Layer 4: Optional LLM reranker ──
    if use_reranker:
        filtered = rerank_chunks(question, filtered, top_k=n_results)

    return filtered, confidence, retrieval_good


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
    """Simple one-shot RAG answer (no corrective retries)."""
    _log(f"[rag] Question: {question[:80]}...")
    """Full RAG pipeline: question → retrieve → prompt → LLM → answer."""
    # 1. Retrieve relevant chunks from ChromaDB
    results, _, _ = _retrieve_with_fallbacks(
        question, store, n_results=n_results, use_expansion=False, use_reranker=False
    )

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


def retrieve_corrective(question: str, store, n_results: int = 5,
                        graph_store=None) -> dict:
    """Corrective retrieval with symbol lookup, expansion, and keyword grading.

    Returns raw chunks for the MCP host (Copilot) to generate the answer.

    Returns:
        {
            "chunks": list[dict],          — filtered chunks with text + metadata
            "confidence": float,           — 0.0-1.0 from distance scoring
            "retrieval_good": bool,        — True if retrieval quality passed
            "total_retrieved": int,        — chunks before filtering
            "total_after_filter": int,     — chunks after all filtering
        }
    """
    _log(f"[retrieve_corrective] Query: '{question[:60]}'")

    results, confidence, retrieval_good = _retrieve_with_fallbacks(
        question, store, graph_store=graph_store, n_results=n_results,
        use_expansion=True, use_reranker=False,
    )

    return {
        "chunks": results,
        "confidence": confidence,
        "retrieval_good": retrieval_good,
        "total_retrieved": len(results),  # after dedup but before free grading
        "total_after_filter": len(results),
    }


def ask_corrective(question: str, store, n_results: int = 5,
                   graph_store=None) -> dict:
    """Corrective RAG with enhanced retrieval and answer grading.

    Returns:
        {
            "answer": str,
            "confident": bool,
            "retries": int,
            "retrieval_confidence": float,
            "relevant_chunks": int,
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
        _log(f"[corrective] Attempt {attempt + 1}/{MAX_RETRIES}: '{current_question[:60]}'")

        # ── Enhanced retrieval ──
        filtered, confidence, retrieval_good = _retrieve_with_fallbacks(
            current_question, store, graph_store=graph_store, n_results=n_results,
            use_expansion=True, use_reranker=(attempt >= 1),  # rerank on retries
        )

        if not filtered:
            if attempt < MAX_RETRIES - 1:
                current_question = rewrite_query(current_question)
                continue
            break

        if not retrieval_good and attempt < MAX_RETRIES - 1:
            current_question = rewrite_query(current_question)
            continue

        # ── Layer 3 (1 LLM call): Grade individual chunks ──
        graded = grade_chunks(question, filtered)
        if graded:
            filtered = graded

        # ── Generate answer ──
        context = format_context(filtered)
        answer = gen_chain.invoke({
            "context": context,
            "question": question,  # always ORIGINAL question for generation
        })

        # ── Layer 4 (1 LLM call): Grade the answer ──
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

        if attempt < MAX_RETRIES - 1:
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
