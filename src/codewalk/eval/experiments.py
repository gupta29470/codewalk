import logging
from typing import Any
import sys

import src.codewalk.rag.retrieval_quality as rq
import src.codewalk.rag.chunk_grader as cg
import src.codewalk.rag.chain as chain_mod

from src.codewalk.eval.evaluator import run_full_evaluation
from src.codewalk.eval.metrics import save_run, compare_runs
from src.codewalk.eval.dataset import EvalSample, load_dataset
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore

logger = logging.getLogger("codewalk.eval")

PATCHABLE_PARAMS: dict[str, dict] = {
    "soft_cutoff": {
        "module": "src.codewalk.rag.retrieval_quality",
        "attr": "SOFT_CUTOFF",
        "description": "Distance below which a chunk is 'good' (lower = stricter)",
    },
    "hard_cutoff": {
        "module": "src.codewalk.rag.retrieval_quality",
        "attr": "HARD_CUTOFF",
        "description": "Distance above which a chunk is dropped entirely",
    },
    "min_good_chunks": {
        "module": "src.codewalk.rag.retrieval_quality",
        "attr": "MIN_GOOD_CHUNKS",
        "description": "Minimum good chunks to pass L2 gate",
    },
    "keyword_overlap": {
        "module": "src.codewalk.rag.chunk_grader",
        "attr": "KEYWORD_OVERLAP_THRESHOLD",
        "description": "Minimum keyword overlap for free chunk grader",
    },
    "max_retries": {
        "module": "src.codewalk.rag.chain",
        "attr": "MAX_RETRIES",
        "description": "Maximum query rewrite retries in full pipeline",
    }
}

def _patch(param_name: str, value: Any) -> Any:
    """Patch a module-level parameter, return the original value.

    Args:
        param_name: Key from PATCHABLE_PARAMS.
        value: New value to set.

    Returns:
        Original value (for restoring later).

    Raises:
        KeyError: If param_name is not in PATCHABLE_PARAMS.
    """
    if param_name not in PATCHABLE_PARAMS:
        raise KeyError(
            f"Unknown param: {param_name}. "
            f"Available: {list(PATCHABLE_PARAMS.keys())}"
        )
    
    info = PATCHABLE_PARAMS[param_name]
    module = sys.modules[info["module"]]
    attr = info["attr"]

    original = getattr(module, attr)
    setattr(module, attr, value)
    logger.info(f"[experiment] Patched {attr}: {original} → {value}")

    return original

def _restore(param_name: str, original_value: Any) -> None:
    """Restore a patched parameter to its original value.

    Args:
        param_name: Key from PATCHABLE_PARAMS.
        original_value: Value returned by _patch().
    """
    info = PATCHABLE_PARAMS[param_name]
    module = sys.modules[info["module"]]
    setattr(module, info["attr"], original_value)
    logger.info(f"[experiment] Restored {info['attr']} → {original_value}")


def run_experiment(store: VectorStore, graph_store: GraphStore | None = None, 
    overrides: dict[str, Any] | None = None, mode: str = "retrieval", 
    samples: list[EvalSample] | None = None, n_results: int = 5, 
    skip_ragas: bool = False, label: str | None = None,) -> dict:
    """Run a single evaluation with temporary parameter overrides.

    Patches parameters → runs eval → restores originals → saves result.

    Args:
        store: VectorStore with indexed codebase.
        graph_store: Optional GraphStore for expansion.
        overrides: Dict of {param_name: value} to patch.
            Keys must be in PATCHABLE_PARAMS.
            Example: {"soft_cutoff": 0.35, "hard_cutoff": 0.65}
        mode: "retrieval" or "full".
        samples: Specific eval samples. None = load all.
        n_results: Chunks per retrieval.
        skip_ragas: Skip RAGAS scoring (internal metrics only).
        label: Label for the saved run. Auto-generated if None.

    Returns:
        The result dict from run_full_evaluation().
    """
    overrides = overrides or {}
    originals: dict[str, Any] = {}

    # Auto-generate label from overrides
    if label is None and overrides:
        parts = [f"{k}_{v}" for k, v in sorted(overrides.items())]
        label = "exp_" + "_".join(parts)

    try:
        # Patch all overrides
        for param_name, value in overrides.items():
            originals[param_name] = _patch(param_name, value)
        
        # Run evaluation with patched values
        result = run_full_evaluation(
            store=store,
            graph_store=graph_store,
            samples=samples,
            mode=mode,
            n_results=n_results,
            skip_ragas=skip_ragas,
        )
    finally:
        # ALWAYS restore, even if eval crashes
        for param_name, original_value in originals.items():
            _restore(param_name, original_value)

    if label:
        save_run(result, label=label)

    logger.info(f"[experiment] Done: {label or 'no label'}")
    return result


def sweep_experiment(param_name: str, values: list[Any], store: VectorStore, graph_store: GraphStore | None = None, 
    mode: str = "retrieval", samples: list[EvalSample] | None = None, 
    n_results: int = 5,  skip_ragas: bool = False,) -> str:
    """Sweep a single parameter across multiple values.

    For each value: patches → runs eval → restores → saves.
    Returns a formatted summary showing the best value.

    Args:
        param_name: Key from PATCHABLE_PARAMS (e.g. "soft_cutoff").
        values: List of values to try (e.g. [0.30, 0.35, 0.40, 0.45, 0.50]).
        store, graph_store, mode, samples, n_results, skip_ragas:
            Same as run_experiment().

    Returns:
        Formatted summary string with results per value + best config.
    """
    if param_name not in PATCHABLE_PARAMS:
        raise KeyError(
            f"Unknown param: {param_name}. "
            f"Available: {list(PATCHABLE_PARAMS.keys())}"
        )
    
    # Load samples once (avoid reloading per iteration)
    if samples is None:
        samples = load_dataset()

    description = PATCHABLE_PARAMS[param_name]["description"]
    logger.info(
        f"[experiment] SWEEP: {param_name} ({description}) "
        f"values={values}"
    )

    results: list[dict] = []

    for value in values:
        label = f"exp_{param_name}_{value}"
        logger.info(f"[experiment] Running {label}...")

        result = run_experiment(
            store=store,
            graph_store=graph_store,
            overrides={param_name: value},
            mode=mode,
            samples=samples,
            n_results=n_results,
            skip_ragas=skip_ragas,
            label=label,
        )

        results.append({
            "value": value,
            "label": label,
            "summary": result["summary"]
        })
    
    return _format_sweep_summary(param_name, results, mode)


def _format_sweep_summary(param_name: str, results: list[dict], mode: str) -> str:
    """Format sweep results into a readable summary.

    Args:
        param_name: The parameter that was swept.
        results: List of {"value", "label", "summary"} per run.
        mode: "retrieval" or "full".

    Returns:
        Formatted string with table + best config.
    """
    lines = [
        f"SWEEP RESULTS: {param_name}",
        f"{'─' * 70}",
    ]

    # Header row depends on mode
    if mode == "retrieval":
        lines.append(
            f"  {'Value':>8s}  {'L1 Conf':>8s}  {'Expansion':>10s}  "
            f"{'Precision':>10s}  {'Recall':>8s}"
        )
    else:
        lines.append(
            f"  {'Value':>8s}  {'L1 Conf':>8s}  {'Expansion':>10s}  "
            f"{'Precision':>10s}  {'Recall':>8s}  "
            f"{'Faithful':>9s}  {'Relevancy':>10s}"
        )

    lines.append(f"  {'─' * 68}")

    best_score = -1.0
    best_value = None

    for entry in results:
        value = entry["value"]
        summary = entry["summary"]

        l1_conf = summary.get("avg_l1_confidence", 0)
        expansion = summary.get("expansion_triggered", 0)
        precision = summary.get("avg_context_precision", 0)
        recall = summary.get("avg_context_recall", 0)

        if mode == "retrieval":
            lines.append(
                f"  {value:>8}  {l1_conf:>8.3f}  {expansion:>10d}  "
                f"{precision:>10.3f}  {recall:>8.3f}"
            )
            score = (precision + recall) / 2
        else:
            faith = summary.get("avg_faithfulness", 0)
            relevancy = summary.get("avg_answer_relevancy", 0)
            lines.append(
                f"  {value:>8}  {l1_conf:>8.3f}  {expansion:>10d}  "
                f"{precision:>10.3f}  {recall:>8.3f}  "
                f"{faith:>9.3f}  {relevancy:>10.3f}"
            )
            score = (precision + recall + faith + relevancy) / 4

        if score > best_score:
            best_score = score
            best_value = value

    lines.append(f"  {'─' * 68}")
    lines.append(f"  BEST: {param_name}={best_value}  (avg score={best_score:.3f})")
    lines.append("")

    return "\n".join(lines)


def toggle_experiment(name: str,
    store: VectorStore,
    graph_store: GraphStore | None = None,
    mode: str = "retrieval",
    samples: list[EvalSample] | None = None,
    n_results: int = 5,
    skip_ragas: bool = False,) -> str:
    """Run a predefined toggle experiment (A/B comparison).

    Available experiments:
      "graph_expansion" — ON vs OFF (pass graph_store=None)
      "grader_comparison" — LLM grader vs free grader
      "n_results" — compare different n_results values

    Args:
        name: Experiment name (see above).
        store, graph_store, mode, samples, n_results, skip_ragas:
            Same as run_experiment().

    Returns:
        Comparison report string.
    """
    if samples is None:
        samples = load_dataset()

    if name == "graph_expansion":
        return _toggle_graph_expansion(
            store, graph_store, mode, samples, n_results, skip_ragas
        )
    elif name == "grader_comparison":
        return _toggle_grader(
            store, graph_store, samples, n_results, skip_ragas
        )
    elif name == "n_results":
        results = []
        for n in [3, 5, 7, 10]:
            label = f"exp_n_results_{n}"
            result = run_experiment(
                store=store,
                graph_store=graph_store,
                overrides={},  # no monkey-patching needed
                mode=mode,
                samples=samples,
                n_results=n,   # ← passed directly
                skip_ragas=skip_ragas,
                label=label,
            )
            results.append({
                "value": n,
                "label": label,
                "summary": result["summary"],
            })

        return _format_sweep_summary("n_results", results, mode)
    else:
        raise ValueError(
            f"Unknown toggle experiment: {name}. "
            f"Available: graph_expansion, grader_comparison, n_results"
        )
    

def _toggle_graph_expansion(store: VectorStore,
    graph_store: GraphStore | None,
    mode: str,
    samples: list[EvalSample],
    n_results: int,
    skip_ragas: bool,) -> str:
    """Compare eval with graph expansion ON vs OFF.

    ON:  pass graph_store to evaluator (expansion can trigger).
    OFF: pass graph_store=None (expansion never triggers).
    """
    # Run A: expansion ON
    result_on = run_experiment(
        store=store,
        graph_store=graph_store,
        overrides={},
        mode=mode,
        samples=samples,
        n_results=n_results,
        skip_ragas=skip_ragas,
        label="exp_expansion_ON",
    )

    # Run B: expansion OFF
    result_off = run_experiment(
        store=store,
        graph_store=None,           # ← no graph store = no expansion
        overrides={},
        mode=mode,
        samples=samples,
        n_results=n_results,
        skip_ragas=skip_ragas,
        label="exp_expansion_OFF",
    )

    return _format_ab_comparison(
        "Graph Expansion", "ON", "OFF",
        result_on["summary"], result_off["summary"], mode,
    )

def _toggle_grader(
    store: VectorStore,
    graph_store: GraphStore | None,
    samples: list[EvalSample],
    n_results: int,
    skip_ragas: bool,
) -> str:
    """Compare free grader (MCP path) vs LLM grader (API path).

    Runs retrieval mode (free grader) and full mode (LLM grader).
    Compares L1-L3 metrics that both modes share.
    Full mode also shows L4 + generation quality as a bonus.
    """
    # Run A: free grader (retrieval mode)
    result_free = run_experiment(
        store=store,
        graph_store=graph_store,
        overrides={},
        mode="retrieval",
        samples=samples,
        n_results=n_results,
        skip_ragas=skip_ragas,
        label="exp_grader_free",
    )

    # Run B: LLM grader (full mode)
    result_llm = run_experiment(
        store=store,
        graph_store=graph_store,
        overrides={},
        mode="full",
        samples=samples,
        n_results=n_results,
        skip_ragas=skip_ragas,
        label="exp_grader_llm",
    )

    return _format_ab_comparison(
        "Chunk Grader", "Free (keyword)", "LLM (structured)",
        result_free["summary"], result_llm["summary"], "mixed",
    )

def _format_ab_comparison(
    experiment_name: str,
    label_a: str,
    label_b: str,
    summary_a: dict,
    summary_b: dict,
    mode: str,
) -> str:
    """Format a generic A vs B comparison.

    Args:
        experiment_name: What was tested (e.g. "Graph Expansion").
        label_a, label_b: Names for the two runs (e.g. "ON", "OFF").
        summary_a, summary_b: Summary dicts from each run.
        mode: "retrieval", "full", or "mixed".

    Returns:
        Formatted comparison string.
    """
    # Collect numeric keys from both summaries
    all_keys = set(summary_a.keys()) | set(summary_b.keys())
    numeric_keys = sorted([
        k for k in all_keys
        if isinstance(summary_a.get(k, 0), (int, float))
        and isinstance(summary_b.get(k, 0), (int, float))
    ])

    lines = [
        f"EXPERIMENT: {experiment_name}",
        f"  A = {label_a}",
        f"  B = {label_b}",
        f"{'─' * 70}",
    ]

    lines.append(
        f"  {'Metric':30s}  {'A':>8s}  {'B':>8s}  {'Delta':>8s}"
    )
    lines.append(f"  {'─' * 66}")

    for key in numeric_keys:
        val_a = summary_a.get(key, 0)
        val_b = summary_b.get(key, 0)
        delta = val_b - val_a
        arrow = "▲" if delta > 0 else "▼" if delta < 0 else "━"

        lines.append(
            f"  {key:30s}  {val_a:>8.3f}  {val_b:>8.3f}  {arrow} {delta:+.3f}"
        )

    lines.append(f"  {'─' * 66}")

    return "\n".join(lines)

def l4_calibration_check(eval_result: dict) -> str:
    """Check L4 answer grader calibration against RAGAS.

    For each question, compares:
      - Our grader: l4_faithful (bool)
      - RAGAS: faithfulness (0.0-1.0)

    Reports agreement rate at different RAGAS thresholds.

    Args:
        eval_result: Dict from run_full_evaluation(mode="full").
            Must have "eval_results" and "ragas_scores".

    Returns:
        Formatted calibration report.
    """
    if eval_result.get("mode") != "full":
        return "L4 calibration requires mode='full' (need L4 grades + RAGAS scores)."
    
    if not eval_result.get("merged"):
        return "No merged results — run with skip_ragas=False."
    
    merged = eval_result["merged"]

    # Check agreement at different RAGAS thresholds
    thresholds = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    lines = [
        "L4 GRADER CALIBRATION CHECK",
        "─" * 60,
        "  Our L4 grader says faithful=True/False.",
        "  RAGAS gives faithfulness=0.0-1.0.",
        "  At what RAGAS threshold do they agree?",
        "",
        f"  {'Threshold':>10s}  {'Agree':>6s}  {'Rate':>6s}  {'Interpretation'}",
        f"  {'─' * 56}",
    ]

    for threshold in thresholds:
        agree = 0
        for merge in merged:
            ragas_pass = merge.faithfulness >= threshold
            our_pass = merge.internal.l4_faithful
            if ragas_pass == our_pass:
                agree += 1
        
        rate = agree / len(merged)

        if rate >= 0.9:
            interp = "Strong match"
        elif rate >= 0.75:
            interp = "Good match"
        elif rate >= 0.6:
            interp = "Moderate"
        else:
            interp = "Poor — grader needs tuning"

        lines.append(
            f"  {threshold:>10.1f}  {agree:>6d}  {rate:>5.0%}  {interp}"
        )

    # Find the "sweet spot" threshold
    lines.append("")

    # Also check: false positives and false negatives at threshold=0.5
    false_positive = 0  # our grader says faithful, RAGAS says unfaithful
    false_negative = 0  # our grader says unfaithful, RAGAS says faithful

    for merge in merged:
        ragas_pass = merge.faithfulness >= 0.5
        our_pass = merge.internal.l4_faithful
        if our_pass and not ragas_pass:
            false_positive += 1
        if not our_pass and ragas_pass:
            false_negative += 1

    lines.append(f"  At threshold=0.5:")
    lines.append(f"    False positives (ours=True, RAGAS<0.5): {false_positive}")
    lines.append(f"    False negatives (ours=False, RAGAS≥0.5): {false_negative}")

    if false_positive > false_negative:
        lines.append("  → Our grader is TOO LENIENT — passing unfaithful answers.")
    elif false_negative > false_positive:
        lines.append("  → Our grader is TOO STRICT — rejecting good answers.")
    else:
        lines.append("  → Our grader is well-calibrated at this threshold.")

    return "\n".join(lines)


