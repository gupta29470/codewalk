"""Review-quality evaluation harness for injected-bug fixtures.

Usage:
    .codewalk-env/bin/python -m src.codewalk.eval.review_eval

Runs the API review pipeline (`review_diff`) against each diff in
`tests/fixtures/review_eval/diffs/` and compares the reported issues to the
expected issues in `tests/fixtures/review_eval/expected/`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from src.codewalk.api import state as api_state
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.review.models import Category, ReviewResult
from src.codewalk.review.reviewer import review_diff


def _detect_language_from_file_path(file_path: str) -> str:
    """Map a file path to a language name using its extension."""
    ext = Path(file_path).suffix.lower().lstrip(".")
    mapping = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "jsx": "javascript",
        "tsx": "typescript",
        "dart": "dart",
        "java": "java",
        "go": "go",
        "rs": "rust",
        "rb": "ruby",
        "php": "php",
        "cs": "csharp",
        "cpp": "cpp",
        "c": "c",
        "kt": "kotlin",
        "swift": "swift",
    }
    return mapping.get(ext, "unknown")

logger = logging.getLogger("codewalk.eval.review")

# Project root is four levels above this file:
# src/codewalk/eval/review_eval.py -> eval -> codewalk -> src -> repo_root
_FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "review_eval"
_LINE_TOLERANCE = 5


@dataclass
class ReviewEvalCase:
    name: str
    diff_text: str
    expected: dict[str, Any]


@dataclass
class ReviewEvalResult:
    case_name: str
    expected: dict[str, Any]
    reported_issues: list[dict[str, Any]] = field(default_factory=list)
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    verdict_correct: bool = False
    error: str | None = None
    raw_verdict: str = ""


def _load_cases() -> list[ReviewEvalCase]:
    diffs_dir = _FIXTURE_ROOT / "diffs"
    expected_dir = _FIXTURE_ROOT / "expected"
    cases = []
    for diff_path in sorted(diffs_dir.glob("*.diff")):
        expected_path = expected_dir / f"{diff_path.stem}.json"
        if not expected_path.exists():
            logger.warning(f"No expected file for {diff_path.name}; skipping")
            continue
        cases.append(ReviewEvalCase(
            name=diff_path.stem,
            diff_text=diff_path.read_text(),
            expected=json.loads(expected_path.read_text()),
        ))
    return cases


def _apply_diff_in_temp_repo(diff_text: str, original_file: Path, rel_path: str) -> Path:
    """Create a temp git repo with original_file, apply diff, stage, return repo root."""
    repo = Path(tempfile.mkdtemp(prefix="codewalk-review-eval-"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "eval@codewalk.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Eval"], cwd=repo, check=True)

    dest = repo / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(original_file.read_text())

    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "original"], cwd=repo, check=True)

    subprocess.run(
        ["git", "apply", "-"],
        input=diff_text,
        cwd=repo,
        check=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    return repo


def _issue_to_dict(issue) -> dict[str, Any]:
    return {
        "title": issue.title,
        "category": issue.category.value if hasattr(issue.category, "value") else issue.category,
        "severity": issue.severity.value if hasattr(issue.severity, "value") else issue.severity,
        "file_path": issue.file_path,
        "line_number": issue.line_number,
        "explanation": issue.explanation,
    }


def _matches(expected: dict[str, Any], reported: dict[str, Any]) -> bool:
    """Relaxed matching: file, category (or list of categories), and line within tolerance."""
    if reported["file_path"] != expected["file_path"]:
        return False
    exp_categories = expected["category"]
    if isinstance(exp_categories, str):
        exp_categories = [exp_categories]
    if reported["category"].lower() not in {c.lower() for c in exp_categories}:
        return False
    exp_line = expected.get("line_number")
    rep_line = reported.get("line_number")
    if exp_line is not None and rep_line is not None:
        if abs(exp_line - rep_line) > _LINE_TOLERANCE:
            return False
    return True


def _score_case(result: ReviewResult, expected: dict[str, Any]) -> ReviewEvalResult:
    # Filter out noisy TEST pre-check issues that are unrelated to injected bugs.
    reported = [
        _issue_to_dict(i)
        for i in result.issues
        if _issue_to_dict(i)["category"].lower() != "test"
    ]

    matched_reported = [False] * len(reported)
    tp = 0

    for exp in [expected]:
        found = False
        for idx, rep in enumerate(reported):
            if matched_reported[idx]:
                continue
            if _matches(exp, rep):
                matched_reported[idx] = True
                found = True
                tp += 1
                break
        if not found:
            pass  # false negative counted once below

    fp = sum(1 for m in matched_reported if not m)
    fn = 1 if tp == 0 else 0
    verdict_correct = result.verdict.value != "approve"

    return ReviewEvalResult(
        case_name=expected.get("title", "unknown"),
        expected=expected,
        reported_issues=reported,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        verdict_correct=verdict_correct,
        raw_verdict=result.verdict.value,
    )


def run_review_evaluation(
    store: VectorStore,
    graph_store: GraphStore | None = None,
    repo_path: str = ".",
    case_filter: list[str] | None = None,
    language_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Run the review benchmark and return a save_run-compatible result dict.

    Args:
        case_filter: If provided, only run cases whose names contain one of these strings.
        language_filter: If provided, only run cases for these languages (e.g., rust, go).
    """
    cases = _load_cases()
    if case_filter:
        cases = [c for c in cases if any(f in c.name for f in case_filter)]
    if language_filter:
        languages = {lang.lower() for lang in language_filter}
        cases = [
            c for c in cases
            if _detect_language_from_file_path(c.expected.get("file_path", "")) in languages
        ]
    results: list[ReviewEvalResult] = []

    for case in cases:
        logger.info(f"[review-eval] Running {case.name}...")
        rel_path = case.expected["file_path"]
        ext = Path(rel_path).suffix or ".py"
        original_path = _FIXTURE_ROOT / "originals" / f"{case.name}{ext}"
        if not original_path.exists():
            logger.warning(f"[review-eval] No original snapshot for {case.name}; skipping")
            continue

        try:
            temp_repo = _apply_diff_in_temp_repo(case.diff_text, original_path, rel_path)
            review_result = review_diff(
                staged=True,
                repo_path=str(temp_repo),
                store=store,
                graph_store=graph_store,
                use_llm=True,
            )
            eval_result = _score_case(review_result, case.expected)
            eval_result.case_name = case.name
        except Exception as e:
            logger.exception(f"[review-eval] {case.name} failed")
            eval_result = ReviewEvalResult(
                case_name=case.name,
                expected=case.expected,
                error=str(e),
                false_negatives=1,
            )
        finally:
            if "temp_repo" in locals():
                import shutil
                shutil.rmtree(temp_repo, ignore_errors=True)

        results.append(eval_result)
        logger.info(
            f"[review-eval] {eval_result.case_name}: "
            f"TP={eval_result.true_positives} FP={eval_result.false_positives} "
            f"FN={eval_result.false_negatives} verdict={eval_result.raw_verdict}"
        )

    total = len(results)
    tp = sum(r.true_positives for r in results)
    fp = sum(r.false_positives for r in results)
    fn = sum(r.false_negatives for r in results)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    verdict_accuracy = sum(1 for r in results if r.verdict_correct) / total if total else 0.0

    report_lines = [
        "┌──────────────────────────────────────────────────────────┐",
        "│ CODEWALK REVIEW QUALITY EVALUATION                       │",
        f"│ Cases: {total:<3}                                             │",
        "├──────────────────────────────────────────────────────────┤",
        f"│ True positives:    {tp}/{total}",
        f"│ False positives:   {fp}",
        f"│ False negatives:   {fn}",
        f"│ Precision:         {precision:.3f}",
        f"│ Recall:            {recall:.3f}",
        f"│ F1:                {f1:.3f}",
        f"│ Verdict accuracy:  {verdict_accuracy:.3f}",
        "├──────────────────────────────────────────────────────────┤",
        "│ PER-CASE RESULTS                                         │",
    ]
    for r in results:
        status = "ERROR" if r.error else ("PASS" if r.true_positives > 0 else "MISS")
        report_lines.append(
            f"│ {r.case_name[:40]:40s} {status:4s} "
            f"TP={r.true_positives} FP={r.false_positives} FN={r.false_negatives} "
            f"{r.raw_verdict[:20]}"
        )
    report_lines.append("├──────────────────────────────────────────────────────────┤")
    report_lines.append("│ PER-LANGUAGE SCORECARD                                   │")
    by_language: dict[str, list[ReviewEvalResult]] = {}
    for r in results:
        lang = _detect_language_from_file_path(r.expected.get("file_path", ""))
        by_language.setdefault(lang, []).append(r)
    for lang in sorted(by_language):
        lang_results = by_language[lang]
        lang_tp = sum(r.true_positives for r in lang_results)
        lang_fp = sum(r.false_positives for r in lang_results)
        lang_fn = sum(r.false_negatives for r in lang_results)
        lang_precision = lang_tp / (lang_tp + lang_fp) if (lang_tp + lang_fp) > 0 else 0.0
        lang_recall = lang_tp / (lang_tp + lang_fn) if (lang_tp + lang_fn) > 0 else 0.0
        lang_f1 = (
            2 * lang_precision * lang_recall / (lang_precision + lang_recall)
            if (lang_precision + lang_recall) > 0 else 0.0
        )
        report_lines.append(
            f"│ {lang:12s} cases={len(lang_results):<3} "
            f"P={lang_precision:.2f} R={lang_recall:.2f} F1={lang_f1:.2f}"
        )
    report_lines.append("└──────────────────────────────────────────────────────────┘")
    report = "\n".join(report_lines)
    print(report)

    # Build a save_run-compatible structure
    summary = {
        "total_cases": total,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "verdict_accuracy": verdict_accuracy,
    }

    eval_result_dict = {
        "mode": "review",
        "summary": summary,
        "report": report,
        "eval_results": [
            {
                "case_name": r.case_name,
                "expected": r.expected,
                "reported_issues": r.reported_issues,
                "true_positives": r.true_positives,
                "false_positives": r.false_positives,
                "false_negatives": r.false_negatives,
                "verdict_correct": r.verdict_correct,
                "raw_verdict": r.raw_verdict,
                "error": r.error,
            }
            for r in results
        ],
    }

    # Persist the review run manually (RAG save_run expects RAG-shaped records).
    eval_dir = Path(repo_path) / ".codewalk" / "eval" / "runs"
    eval_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_path = eval_dir / f"{timestamp}_review_review_baseline.json"
    run_path.write_text(json.dumps(eval_result_dict, indent=2, default=str))
    logger.info(f"[review-eval] Saved run to {run_path}")
    return eval_result_dict


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Codewalk review quality evaluation")
    parser.add_argument(
        "--cases",
        nargs="+",
        help="Run only cases whose names contain any of these substrings",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        help="Run only cases for these languages (e.g., rust go swift)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Global state is used by review helpers (e.g. doc search path). Anchor it to
    # the project repo so vector/doc stores resolve correctly while the git diff
    # is evaluated inside per-case temporary repos.
    api_state._repo_path = os.path.abspath(".")
    store = VectorStore(".codewalk/chroma")
    store.create_collection("codewalk")
    graph_store = GraphStore(".codewalk/graph.duckdb")
    run_review_evaluation(
        store=store,
        graph_store=graph_store,
        repo_path=".",
        case_filter=args.cases,
        language_filter=args.languages,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
