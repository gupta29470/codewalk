"""RAGAS-based evaluation harness for retrieval and answer quality."""
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import get_llm, settings
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.rag.retrieval_quality import filter_by_distance, is_retreival_good
from src.codewalk.rag.chunk_grader import grade_chunks, grade_chunks_free
from src.codewalk.rag.answer_grader import grade_answer
from src.codewalk.rag.graph_expansion import expand_via_graph
from src.codewalk.rag.query_rewriter import rewrite_query
from src.codewalk.rag.chain import format_context, MAX_RETRIES
from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT
from src.codewalk.eval.dataset import EvalSample, load_dataset
from src.codewalk.eval.metrics import save_run

try:
    from ragas import evaluate
except Exception:  # pragma: no cover
    evaluate = None

logger = logging.getLogger("codewalk.eval")

@dataclass
class InternalMetrics:
    """Per-question internal metrics captured during pipeline replay."""
    # Layer 1 — distance filter
    l1_total_retreived: int = 0 # chunks from store.search()
    l1_after_filter: int = 0 # chunks after HARD_CUTOFF drop
    l1_confidence: float = 0.0 # good_count / total
    l1_dropped: int = 0 # chunks removed by distance

    # Layer 2 — retrieval quality gate
    l2_retrieval_good: bool = False     # did it pass the quality check?

    # Layer 2b — graph expansion
    l2b_expansion_used: bool = False    # was graph expansion triggered?
    l2b_chunks_added: int = 0           # how many neighbor chunks added
    l2b_confidence_after: float = 0.0   # confidence after expansion

    # Layer 3 — chunk grading
    l3_chunks_before: int = 0           # chunks entering L3
    l3_chunks_after: int = 0            # chunks surviving L3
    l3_grader_type: str = ""            # "llm" or "free"

    # Layer 4 — answer grading
    l4_faithful: bool = False
    l4_relevant: bool = False
    l4_reason: str = ""

    # Retry tracking
    retries: int = 0
    final_question: str = ""            # question after rewrites

    # Performance
    latency_ms: float = 0.0             # total time for this question

@dataclass
class EvalResult:
    """Complete evaluation result for one question."""
    question: str
    ground_truth: str
    answer: str                          # LLM-generated answer
    contexts: list[str]                  # retrieved chunk texts (for RAGAS)
    category: str                        # from EvalSample
    source_file: str                     # which JSON dataset file
    internal: InternalMetrics            # our pipeline metrics


def collect_retrieval_result(
    sample: EvalSample,
    store: VectorStore,
    graph_store: GraphStore | None = None,
    n_results: int = 5,
) -> EvalResult:
    """Run MCP-path retrieval, capture retrieval-only metrics.

    Mirrors retrieve_corrective() — NO LLM calls.

    Args:
        sample: EvalSample with question and ground truth.
        store: Initialized VectorStore (ChromaDB with indexed codebase).
        graph_store: Optional GraphStore (DuckDB) for graph expansion.
        n_results: Number of chunks to retrieve.
    
    Returns:
        EvalResult with contexts (chunks) but answer="" (MCP doesn't generate).
    """
    metrics = InternalMetrics()
    start = time.perf_counter()
    question = sample.question

    # ── Layer 1: Retrieve + distance filter ──
    results = store.search(question, n_results=n_results)
    if not results:
        metrics.latency_ms = (time.perf_counter() - start) * 1000
        return EvalResult(
            question=question,
            ground_truth=sample.ground_truth,
            answer="",      # MCP doesn't generate — Copilot does
            contexts=[],
            category=sample.category,
            source_file=sample.source_file,
            internal=metrics,
        )  

    metrics.l1_total_retreived = len(results)
    filtered, confidence = filter_by_distance(results)
    metrics.l1_after_filter = len(filtered)
    metrics.l1_confidence = confidence
    metrics.l1_dropped = len(results) - len(filtered)

    # ── Layer 2: Retrieval quality gate ──
    retrieval_good = is_retreival_good(confidence, len(filtered))
    metrics.l2_retrieval_good = retrieval_good

    if not retrieval_good:
        # ── Layer 2b: Graph expansion ── 
        if graph_store and filtered:
            before_expansion = len(filtered)
            expanded = expand_via_graph(
                filtered, store, question, graph_store
            )

            if len(expanded) > before_expansion:
                metrics.l2b_expansion_used = True
                metrics.l2b_chunks_added = len(expanded) - before_expansion
                filtered = expanded
                confidence = max(confidence, 0.35)
                metrics.l2b_confidence_after = confidence 

        if not filtered:
            filtered = results

    # ── Layer 3: Chunk grading ──
    metrics.l3_chunks_before = len(filtered)
    graded = grade_chunks_free(question, filtered)
    metrics.l3_grader_type = "free"
    if graded:
        filtered = graded
    metrics.l3_chunks_after = len(filtered)

    # ── No L4, no generation — MCP returns chunks to Copilot ──
    metrics.latency_ms = (time.perf_counter() - start) * 1000

    return EvalResult(
        question=question,
        ground_truth=sample.ground_truth,
        answer="",          # empty — MCP doesn't generate answers
        contexts=[r["text"] for r in filtered],
        category=sample.category,
        source_file=sample.source_file,
        internal=metrics,
    )   


def collect_full_result(
    sample: EvalSample,
    store: VectorStore,
    graph_store: GraphStore | None = None,
    n_results = 5
) -> EvalResult:
    """Run API-path full pipeline, capture all metrics.

    Mirrors ask_corrective() — 3+ LLM calls per question.
    Captures L1-L4 metrics + generated answer.

    Args:
        sample: EvalSample with question and ground truth.
        store: Initialized VectorStore (ChromaDB with indexed codebase).
        graph_store: Optional GraphStore (DuckDB) for graph expansion.
        n_results: Number of chunks to retrieve.

    Returns:
        EvalResult with answer + contexts + full internal metrics.
    """
    metrics = InternalMetrics()
    start = time.perf_counter()
    question = sample.question
    current_question = question

    # Build generation chain (same as ask_corrective)
    llm = get_llm(temperature=0)
    prompt = ChatPromptTemplate([
        ("system", SYSTEM_PROMPT),
        ("human", QUESTION_PROMPT),
    ])
    
    chain = prompt | llm | StrOutputParser()

    best_answer = None
    best_contexts: list[str] = []

    for attempt in range(MAX_RETRIES):
        # ── Layer 1: Retrieve + distance filter ──
        results = store.search(current_question, n_results=n_results)
        if not results:
            if attempt < MAX_RETRIES - 1:
                current_question = rewrite_query(current_question)
                continue
            break

        metrics.l1_total_retreived = len(results)
        filtered, confidence = filter_by_distance(results)
        metrics.l1_after_filter = len(filtered)
        metrics.l1_confidence = confidence
        metrics.l1_dropped = len(results) - len(filtered)

        # ── Layer 2: Retrieval quality gate ──
        retrieval_good = is_retreival_good(confidence, len(filtered))
        metrics.l2_retrieval_good = retrieval_good

        if not retrieval_good:
            # ── Layer 2b: Graph expansion ──
            if graph_store and filtered:
                before_expansion = len(filtered)
                expanded = expand_via_graph(
                    filtered, store, current_question, graph_store
                )
                if len(expanded) > before_expansion:
                    metrics.l2b_expansion_used = True
                    metrics.l2b_chunks_added = len(expanded) - before_expansion
                    filtered = expanded
                    confidence = max(confidence, 0.35)
                    metrics.l2b_confidence_after = confidence

            # Re-check after expansion
            if not is_retreival_good(confidence, len(filtered)):
                if attempt < MAX_RETRIES - 1:
                    current_question = rewrite_query(current_question)
                    continue

                # Last attempt — use whatever we have
                if not filtered:
                    filtered = results

        # ── Layer 3: LLM chunk grading ──
        metrics.l3_chunks_before = len(filtered)
        graded = grade_chunks(question, filtered)
        metrics.l3_grader_type = "llm"
        if graded:
            filtered = graded
        metrics.l3_chunks_after = len(filtered)

        # ── Generate answer ──
        context = format_context(filtered)
        answer = chain.invoke({
            "context": context,
            "question": question,   # always ORIGINAL question
        })

        # ── Layer 4: Grade the answer ──
        grade = grade_answer(question, context, answer)
        metrics.l4_faithful = grade.faithful
        metrics.l4_relevant = grade.relevant
        metrics.l4_reason = grade.reason

        if grade.faithful and grade.relevant:
            metrics.retries = attempt
            metrics.final_question = current_question
            metrics.latency_ms = (time.perf_counter() - start) * 1000

            return EvalResult(
                question=question,
                ground_truth=sample.ground_truth,
                answer=answer,
                contexts=[r["text"] for r in filtered],
                category=sample.category,
                source_file=sample.source_file,
                internal=metrics,
            )
        
        # Save best effort
        best_answer = answer
        best_contexts = [r["text"] for r in filtered]

        if attempt < MAX_RETRIES - 1:
            current_question = rewrite_query(current_question)

    # ── Exhausted retries ──
    metrics.retries = MAX_RETRIES 
    metrics.final_question = current_question
    metrics.latency_ms = (time.perf_counter() - start) * 1000

    return EvalResult(
        question=question,
        ground_truth=sample.ground_truth,
        answer=best_answer or "Could not generate an answer.",
        contexts=best_contexts,
        category=sample.category,
        source_file=sample.source_file,
        internal=metrics,
    )

def collect_rag_results(
    samples: list[EvalSample], 
    store: VectorStore, 
    graph_store: GraphStore | None = None, 
    mode: str = "retrieval", n_results: int = 5,) -> list[EvalResult]:
    """Run all eval samples through the pipeline.

    Args:
        samples: List of EvalSample to evaluate.
        store: VectorStore with indexed codebase.
        graph_store: Optional GraphStore for expansion.
        mode: "retrieval" (MCP path, 0 LLM) or "full" (API path, 3+ LLM).
        n_results: Chunks per retrieval.

    Returns:
        List of EvalResult with contexts + internal metrics.
    """
    collector = (
        collect_retrieval_result if mode == "retrieval"
         else collect_full_result
    )
    results = []
    total = len(samples)

    for index, sample in enumerate(samples, 1):
        logger.info(f"[eval] [{mode}] {index}/{total}: {sample.question[:60]}...")
        result = collector(sample, store, graph_store, n_results)
        results.append(result)
        logger.info(
            f"[eval] {index}/{total} done — "
            f"L1 conf={result.internal.l1_confidence:.2f}, "
            f"L3 survived={result.internal.l3_chunks_after}"
        )

    return results

def run_ragas_evaluation(
    eval_results: list[EvalResult], 
    mode: str = "retrieval", 
    metrics: list | None = None,) -> dict[str, Any]:
    """Feed collected results to RAGAS for external scoring.

    Args:
        eval_results: Output from collect_rag_results().
        mode: "retrieval" = only context metrics, "full" = all 4 metrics.
        metrics: RAGAS metrics override. None = auto-select based on mode.

    Returns:
        Dict with RAGAS scores + per-question breakdown.

    Raises:
        ImportError: If RAGAS is not installed.
    """
    if evaluate is None:
        raise ImportError(
            "RAGAS is required for run_ragas_evaluation(). "
            "Install it with: pip install ragas"
        )

    from ragas.metrics import (
        context_precision, # Are the retrieved docs relevant to the question?
        context_recall, # Do the retrieved docs contain the needed info?
        faithfulness, # Is the answer supported by retrieved docs?
        answer_relevancy, # Does the answer address the question?
    )

    from datasets import Dataset

    if metrics is None:
        if mode == "retrieval":
            metrics = [context_precision, context_recall]
        else:
            metrics = [context_precision, context_recall, faithfulness, answer_relevancy]

    # Build the HuggingFace Dataset that RAGAS expects
    ragas_data = {
        "question": [result.question for result in eval_results],
        "answer": [result.answer or "N/A" for result in eval_results],
        "contexts": [result.contexts for result in eval_results],
        "ground_truth": [result.ground_truth for result in eval_results],
    }

    dataset = Dataset.from_dict(ragas_data)

    # Run RAGAS evaluation
    ragas_result = evaluate(dataset, metrics=metrics)

    return ragas_result

@dataclass
class MergedResult:
    """One question's complete evaluation: RAGAS + internal."""
    question: str
    category: str
    source_file: str

    # RAGAS scores (per-question)
    context_precision: float
    context_recall: float
    faithfulness: float
    answer_relevancy: float

    # Internal metrics
    internal: InternalMetrics

    # I/O
    answer: str
    ground_truth: str
    contexts: list[str]

def merge_results(eval_results: list[EvalResult], 
                  ragas_result) -> list[MergedResult]:
    """Combine RAGAS per-question scores with internal metrics.

    Args:
        eval_results: Our collected results with internal metrics.
        ragas_result: Output from run_ragas_evaluation().

    Returns:
        List of MergedResult — one per question, with everything.
    """
    df = ragas_result.to_pandas()
    merged = []

    for index, eval_result in enumerate(eval_results):
        row = df.iloc[index]
        merged.append(MergedResult(
            question=eval_result.question,
            category=eval_result.category,
            source_file=eval_result.source_file,
            context_precision=float(row.get("context_precision", 0)),
            context_recall=float(row.get("context_recall", 0)),
            faithfulness=float(row.get("faithfulness", 0)),
            answer_relevancy=float(row.get("answer_relevancy", 0)),
            internal=eval_result.internal,
            answer=eval_result.answer,
            ground_truth=eval_result.ground_truth,
            contexts=eval_result.contexts,
        ))

    return merged

def format_report(merged: list[MergedResult]) -> str:
    """Generate the evaluation report string.

    Sections:
      1. RAGAS aggregate scores
      2. Internal layer stats (averages)
      3. Worst questions (lowest faithfulness)
      4. By-category breakdown
      5. L4 grader vs RAGAS agreement check
    """
    length = len(merged)

    # ── RAGAS aggregates ──
    avg_precision = sum(merge.context_precision for merge in merged) / length
    avg_recall = sum(merge.context_recall for merge in merged) / length
    avg_faith = sum(merge.faithfulness for merge in merged) / length
    avg_relevancy = sum(merge.answer_relevancy for merge in merged) / length

    # ── Internal layer stats ──
    avg_l1_conf = sum(merge.internal.l1_confidence for merge in merged) / length
    low_conf_count = sum(1 for merge in merged if merge.internal.l1_confidence < 0.35)
    expansion_count = sum(1 for merge in merged if merge.internal.l2b_expansion_used)

    l3_survival_rates = []
    for merge in merged:
        if merge.internal.l3_chunks_before > 0:
            l3_survival_rates.append(
                merge.internal.l3_chunks_after / merge.internal.l3_chunks_before
            )
    avg_l3_survival = sum(l3_survival_rates) / len(l3_survival_rates) if l3_survival_rates else 0

    avg_retries = sum(merge.internal.retries for merge in merged) / length
    max_retry_question = max(merged, key=lambda merge: merge.internal.retries)

    # ── L4 vs RAGAS agreement ──
    #   Our grader says faithful=True/False.
    #   RAGAS says faithfulness=0.0-1.0.
    #   We check: does our binary agree with RAGAS at threshold 0.5?
    agree = 0
    for merge in merged:
        ragas_pass = merge.faithfulness >= 0.5
        our_pass = merge.internal.l4_faithful
        if ragas_pass == our_pass:
            agree += 1
    
    agreement_rate = agree / length

    # ── Worst questions ──
    worst = sorted(merged, key=lambda merge: merge.faithfulness)[:5]

    # ── By category ──
    categories: dict[str, list[MergedResult]] = {}

    for merge in merged:
        categories.setdefault(merge.category, []).append(merge)

    # ── Build report ──
    lines = [
        "┌──────────────────────────────────────────────────────────┐",
        "│ CODEWALK RAG EVALUATION REPORT                           │",
        f"│ Questions: {length}   Model: {settings.llm_model}",
        "├──────────────────────────────────────────────────────────┤",
        "│ RAGAS METRICS                                            │",
        f"│ Context Precision:  {avg_precision:.3f}",
        f"│ Context Recall:     {avg_recall:.3f}",
        f"│ Faithfulness:       {avg_faith:.3f}",
        f"│ Answer Relevancy:   {avg_relevancy:.3f}",
        "├──────────────────────────────────────────────────────────┤",
        "│ INTERNAL LAYER STATS                                     │",
        f"│ Avg L1 confidence:  {avg_l1_conf:.2f}   ({low_conf_count}/{length} had conf<0.35)",
        f"│ Graph expansion:    triggered {expansion_count}/{length} ({expansion_count*100//length}%)",
        f"│ L3 chunk survival:  {avg_l3_survival:.0%} avg",
        f"│ L4 vs RAGAS agree:  {agreement_rate:.0%} ({agree}/{length})",
        f"│ Avg retries:        {avg_retries:.1f}   (max={max_retry_question.internal.retries} on '{max_retry_question.question[:40]}')",
        "├──────────────────────────────────────────────────────────┤",
        "│ WORST QUESTIONS (lowest faithfulness)                    │",
    ]

    for merge in worst:
        lines.append(f"│   {merge.faithfulness:.2f} — \"{merge.question[:50]}\"")
        lines.append(
            f"│     L1={merge.internal.l1_confidence:.2f} "
            f"exp={'Y' if merge.internal.l2b_expansion_used else 'N'} "
            f"L3={merge.internal.l3_chunks_after}/{merge.internal.l3_chunks_before} "
            f"L4={'✓' if merge.internal.l4_faithful else '✗'}"
        )
    
    lines.append("├──────────────────────────────────────────────────────────┤")
    lines.append("│ BY CATEGORY                                              │")

    for category, items in sorted(categories.items()):
        cat_prec = sum(m.context_precision for m in items) / len(items)
        cat_faith = sum(m.faithfulness for m in items) / len(items)
        lines.append(f"│   {category:12s}  precision={cat_prec:.2f}  faithfulness={cat_faith:.2f}  (n={len(items)})")

    lines.append("└──────────────────────────────────────────────────────────┘")

    return "\n".join(lines)

def run_full_evaluation(store: VectorStore, graph_store: GraphStore | None = None,
    samples: list[EvalSample] | None = None, mode: str = "retrieval",
     n_results: int = 5, skip_ragas: bool = False, repo_path: str = ".") -> dict:
    """Run complete evaluation: dataset → pipeline replay → RAGAS → report.

    Args:
        store: VectorStore with indexed codebase.
        graph_store: Optional GraphStore for graph expansion.
        samples: Specific samples to evaluate. None = load all from dataset.
        mode: "retrieval" (MCP path, 0 LLM) or "full" (API path, 3+ LLM).
        n_results: Chunks per retrieval.
        skip_ragas: True = skip RAGAS scoring (just collect internal metrics).
                    Useful for quick sanity checks or when no API key.

    Returns:
        {
            "eval_results": list[EvalResult],
            "ragas_scores": dict | None,  # None if skip_ragas
            "merged": list[MergedResult] | None,
            "report": str,
            "mode": "retrieval" | "full",
            "summary": {
                "total_questions": int,
                "mode": str,
                "avg_context_precision": float,
                "avg_faithfulness": float | None,   # None if mode=retrieval
                "model": str,
            }
        }
    """
    if samples is None:
        samples = load_dataset()

    logger.info(f"[eval] Starting {mode} evaluation: {len(samples)} questions")

    # Step 1: Run all questions through instrumented pipeline
    eval_results = collect_rag_results(
        samples, store, graph_store, mode=mode, n_results=n_results
    )

    if skip_ragas:
        # Internal-only report
        report = _internal_only_report(eval_results, mode)
        result = {
            "eval_results": eval_results,
            "ragas_scores": None,
            "merged": None,
            "report": report,
            "mode": mode,
            "summary": _build_summary(eval_results, None, mode),
        }
        save_run(result, repo_path=repo_path)
        return result

    # Step 2: Feed to RAGAS (mode controls which metrics)
    ragas_scores = run_ragas_evaluation(eval_results, mode=mode)

    # Step 3: Merge RAGAS + internal
    merged = merge_results(eval_results, ragas_scores)

    # Step 4: Format report
    report = format_report(merged)

    logger.info(f"[eval] {mode} evaluation complete")
    print(report)

    result = {
        "eval_results": eval_results,
        "ragas_scores": ragas_scores,
        "merged": merged,
        "report": report,
        "mode": mode,
        "summary": _build_summary(eval_results, ragas_scores, mode),
    }
    save_run(result, repo_path=repo_path)
    return result
    
def _build_summary(eval_results: list[EvalResult], ragas_scores: dict | None, mode: str,):
    """Build a summary dict for quick access."""
    length = len(eval_results)

    summary = {
        "total_questions": length,
        "mode": mode,
        "model": settings.llm_model,
        "avg_l1_confidence": sum(r.internal.l1_confidence for r in eval_results) / length,
        "avg_retries": sum(r.internal.retries for r in eval_results) / length,
        "expansion_triggered": sum(1 for r in eval_results if r.internal.l2b_expansion_used),
    }

    if ragas_scores:
        summary["avg_context_precision"] = ragas_scores.get("context_precision", 0)
        summary["avg_context_recall"] = ragas_scores.get("context_recall", 0)
        if mode == "full":
            summary["avg_faithfulness"] = ragas_scores.get("faithfulness", 0)
            summary["avg_answer_relevancy"] = ragas_scores.get("answer_relevancy", 0)

    return summary

def _internal_only_report(eval_results: list[EvalResult], mode: str) -> str:
    """Quick report when RAGAS is skipped — internal metrics only."""
    length = len(eval_results)

    avg_conf = sum(result.internal.l1_confidence for result in eval_results) / length
    expansion_count = sum(1 for result in eval_results if result.internal.l2b_expansion_used)

    lines = [
        f"CODEWALK INTERNAL-ONLY EVAL — mode={mode} (RAGAS skipped)",
        f"Questions: {length}  Model: {settings.llm_model if mode == 'full' else 'N/A (MCP)'}",
        f"Avg L1 confidence:  {avg_conf:.2f}",
        f"Graph expansion:    {expansion_count}/{length}",
    ]

    if mode == "full":
        # API path has L4 and retries
        faithful_count = sum(1 for result in eval_results if result.internal.l4_faithful)
        relevant_count = sum(1 for result in eval_results if result.internal.l4_relevant)
        lines.append(f"L4 faithful:        {faithful_count}/{length} ({faithful_count*100//length}%)")
        lines.append(f"L4 relevant:        {relevant_count}/{length} ({relevant_count*100//length}%)")
        lines.append(f"Avg retries:        {sum(r.internal.retries for r in eval_results) / length:.1f}")
    else:
        lines.append("(MCP mode: no L4 grading, no retries, no answer generation)")

    return "\n".join(lines)











    


        







    

     
        




        




