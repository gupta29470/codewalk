"""Save, load, compare, and trend evaluation runs."""
import json
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import asdict
from typing import Any

from src.codewalk.config import settings

logger = logging.getLogger("codewalk.eval")

def _eval_dir(repo_path: str = ".") -> Path:
    """Return .codewalk/eval/ for the current repo, creating if needed."""
    base = Path(repo_path) / ".codewalk" / "eval" / "runs"
    base.mkdir(parents=True, exist_ok=True)
    return base.parent   # returns .codewalk/eval/

def _run_filename(mode: str, label: str | None = None) -> str:
    """Generate a timestamped filename for this run.

    Format: 2026-06-03_142530_retrieval_ollama.json
            date     time   mode     label

    Args:
        mode: "retrieval" or "full"
        label: Optional label (e.g. model name, experiment name).
               Defaults to settings.llm_model for full, "mcp" for retrieval.

    Returns:
        Filename string (no path).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if label is None:
        label = settings.llm_model if mode == "full" else "mcp"
    # Sanitize label — no slashes or spaces
    safe_label = label.replace("/", "-").replace(" ", "_")
    return f"{timestamp}_{mode}_{safe_label}.json"

def save_run(eval_result: dict, label: str | None = None, repo_path: str = ".") -> Path:
    """Save an evaluation run to .codewalk/eval/runs/.

    Args:
        eval_result: The dict returned by run_full_evaluation().
            Must have: "summary", "mode", "report", "eval_results".
        label: Optional label for the filename.
        repo_path: Repo root for eval output directory.

    Returns:
        Path to the saved JSON file.
    """
    eval_dir = _eval_dir(repo_path)
    mode = eval_result["mode"]
    filename = _run_filename(mode, label)
    filepath = eval_dir / "runs" / filename

    # Build serializable dict
    run_data = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "label": label or (settings.llm_model if mode == "full" else "mcp"),
        "summary": eval_result["summary"],
        "report": eval_result["report"],
        "per_question": [],
    }

    # Serialize per-question results
    for er in eval_result["eval_results"]:
        run_data["per_question"].append({
            "question": er.question,
            "ground_truth": er.ground_truth,
            "answer": er.answer,
            "contexts": er.contexts,
            "category": er.category,
            "source_file": er.source_file,
            "internal": asdict(er.internal),
        })

    # If RAGAS scores exist, add them
    if eval_result.get("ragas_scores"):
        # ragas_result is dict-like, convert to plain dict
        run_data["ragas_scores"] = {
            key: value for key, value in eval_result["ragas_scores"].items()
            if isinstance(value, (int, float))
        }

    # Write JSON
    filepath.write_text(json.dumps(run_data, indent=2, default=str))
    logger.info(f"[eval] Saved run to {filepath}")
    
    # Update history index
    _append_to_history(eval_dir, filename, run_data["label"], run_data["summary"])

    return filepath

def _append_to_history(eval_dir: Path, filename: str, label: str, summary: dict) -> None:
    """Append a run entry to history.json.

    Args:
        eval_dir: .codewalk/eval/
        filename: Just the filename (not full path).
        label: Run label used in the filename.
        summary: The summary dict from _build_summary().
    """
    history_path = eval_dir / "history.json"

    # Load existing history or start fresh
    if history_path.exists():
        history = json.loads(history_path.read_text())
    else:
        history = []

    # Append this run
    history.append({
        "filename": filename,
        "label": label,
        "timestamp": datetime.now().isoformat(),
        **summary,
    })

    history_path.write_text(json.dumps(history, indent=2))

def load_run(filename: str, repo_path: str = ".") -> dict:
    """Load a saved evaluation run.

    Args:
        filename: Just the filename (e.g. "2026-06-03_142530_retrieval_mcp.json")
                  or a full path.
        repo_path: Repo root for eval output directory.

    Returns:
        The saved run dict (same structure as save_run wrote).

    Raises:
        FileNotFoundError: If the run file doesn't exist.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = _eval_dir(repo_path) / "runs" / filename

    if not path.exists():
        raise FileNotFoundError(f"Run file not found: {path}")

    return json.loads(path.read_text())

def list_runs(repo_path: str = ".") -> list[dict]:
    """List all saved runs from history.json.

    Args:
        repo_path: Repo root for eval output directory.

    Returns:
        List of summary dicts, newest first.
    """
    history_path = _eval_dir(repo_path) / "history.json"
    if not history_path.exists():
        return []

    history = json.loads(history_path.read_text())
    return list(reversed(history))  # newest first

def compare_runs(filename_a: str, filename_b: str, repo_path: str = ".") -> str:
    """Compare two saved runs and return a formatted delta report.

    Args:
        filename_a: Baseline run filename.
        filename_b: New run filename (compared against baseline).
        repo_path: Repo root for eval output directory.

    Returns:
        Formatted comparison string.
    """
    run_a = load_run(filename_a, repo_path=repo_path)
    run_b = load_run(filename_b, repo_path=repo_path)

    summary_a = run_a["summary"]
    summary_b = run_b["summary"]

    # Collect all numeric keys present in either summary
    all_keys = set(summary_a.keys()) | set(summary_b.keys())
    numeric_keys = [
        key for key in all_keys
        if isinstance(summary_a.get(key, 0), (int, float))
        and isinstance(summary_b.get(key, 0), (int, float))
    ]

    lines = [
        "┌──────────────────────────────────────────────────────────┐",
        "│ COMPARISON REPORT                                        │",
        f"│ A: {filename_a[:50]}",
        f"│ B: {filename_b[:50]}",
        "├──────────────────────────────────────────────────────────┤",
    ]

    for key in sorted(numeric_keys):
        val_a = summary_a.get(key, 0)
        val_b = summary_b.get(key, 0)
        delta = val_b - val_a


        if delta > 0:
            arrow = "▲"
        elif delta < 0:
            arrow = "▼"
        else:
            arrow = "━"

        lines.append(
            f"│ {key:30s}  A={val_a:<8.3f}  B={val_b:<8.3f}  {arrow} {delta:+.3f}"
        )

    lines.append("└──────────────────────────────────────────────────────────┘")

    # Per-question comparison: find biggest movers
    if run_a.get("per_question") and run_b.get("per_question"):
        lines.append("")
        lines.append("BIGGEST MOVERS (L1 confidence change):")
        movers = []

        for question_a, question_b in zip(run_a["per_question"], run_b["per_question"]):
            if question_a["question"] == question_b["question"]:
                delta_conf = (
                    question_b["internal"]["l1_confidence"]
                    - question_a["internal"]["l1_confidence"]
                )
                movers.append((question_a["question"], delta_conf))

        movers.sort(key=lambda x: abs(x[1]), reverse=True)
        for question, delta_conf in movers[:5]:
            arrow = "▲" if delta_conf > 0 else "▼"
            lines.append(f"  {arrow} {delta_conf:+.2f}  {question[:60]}")
    
    return "\n".join(lines)

def trend(metric_key: str, mode: str | None = None, repo_path: str = ".") -> str:
    """Show a metric's value across all saved runs.

    Args:
        metric_key: Key from summary dict (e.g. "avg_context_precision").
        mode: Filter runs by mode ("retrieval" or "full"). None = all.
        repo_path: Repo root for eval output directory.

    Returns:
        Formatted trend string.
    """
    runs = list_runs(repo_path)  # newest first
    runs.reverse()      # chronological for display

    if mode:
        runs = [run for run in runs if run.get("mode") == mode]

    if not runs:
        return f"No runs found{' for mode=' + mode if mode else ''}."
    
    lines = [f"TREND: {metric_key} ({len(runs)} runs)"]
    lines.append("─" * 60)

    prev_val = None
    for run in runs:
        val = run.get(metric_key)
        if val is None:
            continue

        if prev_val is not None:
            delta = val - prev_val
            arrow = "▲" if delta > 0 else "▼" if delta < 0 else "━"
            delta_str = f"  {arrow} {delta:+.3f}"
        else:
            delta_str = "  (baseline)"

        timestamp = run.get("timestamp", "?")[:10]  # just the date
        label = run.get("filename", "")[:40]
        lines.append(f"  {timestamp}  {val:.3f}{delta_str}  {label}")

        prev_val = val

    return "\n".join(lines)



                    

