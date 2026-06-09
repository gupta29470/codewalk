from __future__ import annotations
import json
from src.codewalk.review.models import ReviewResult, Issue, Severity, Category, Confidence
from src.codewalk.core.reflect import reflect

# Critic prompt — the only review-specific thing here
REVIEW_CRITIC_PROMPT = """You are a senior tech lead reviewing another engineer's code review.
Your job: identify what the reviewer MISSED, what they got WRONG, and what is a false positive.

Given: the original git diff + the initial review (list of issues).

Output a JSON object:
{
  "missed": [
    {"title": "...", "explanation": "...", "severity": "CRITICAL|WARNING|SUGGESTION",
     "category": "bug|security|style|design|...", "file_path": "...", "line_number": null}
  ],
  "false_positives": ["issue title that is actually fine and why in one sentence"],
  "summary_critique": "one sentence: overall quality of the initial review"
}

Rules:
- Only flag genuinely missed issues — not stylistic disagreements
- Only flag false positives if you are certain the flagged code is actually correct
- If the initial review is thorough, return empty missed/false_positives arrays"""

def _apply_review_critique(result: ReviewResult, raw_critique: str) -> ReviewResult:
    """improve_fn for code review: add missed issues, remove false positives."""
    raw = raw_critique
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    
    try:
        critique = json.loads(raw)
    except json.JSONDecodeError:
        return result
    
    new_issues = list(result.issues)

    sev_map = {"CRITICAL": Severity.CRITICAL, "WARNING": Severity.WARNING, "SUGGESTION": Severity.SUGGESTION}
    cat_map = {category.value: category for category in Category}

    for missed in critique.get("missed", []):
        new_issues.append(Issue(
            severity=sev_map.get(missed.get("severity", "WARNING"), Severity.WARNING),
            category=cat_map.get(missed.get("category", "bug"), Category.BUG),
            file_path=missed.get("file_path", ""),
            line_number=missed.get("line_number"),
            title=missed.get("title", ""),
            explanation=missed.get("explanation", ""),
            suggestion=None,
            code_snippet=None,
            confidence=Confidence.MEDIUM,   # reflector-found issues get medium confidence
        ))

    fp_texts = [fp.strip().lower() for fp in critique.get("false_positives", [])]

    def _is_false_positive(issue: Issue, fp_texts: list[str]) -> bool:
        title_lower = issue.title.lower()
        for fp in fp_texts:
            # Exact match, or the issue title is contained in / contains the FP text
            if title_lower == fp or title_lower in fp or fp in title_lower:
                return True
        return False

    new_issues = [issue for issue in new_issues if not _is_false_positive(issue, fp_texts)]

    critique_note = critique.get("summary_critique", "")
    return ReviewResult(
        issues=new_issues,
        summary=result.summary + (f"\n\n[Reflection] {critique_note}" if critique_note else ""),
        verdict=result.verdict,
        verdict_reason=result.verdict_reason,
        files_reviewed=result.files_reviewed,
        lines_added=result.lines_added,
        lines_removed=result.lines_removed,
        diff_text=result.diff_text,  # carry through for chained iterations
    )

def reflect_on_review(initial_result: ReviewResult, 
    diff_text: str, iterations: int = 1) -> ReviewResult:
    """Thin wrapper: calls core/reflect.py with review-specific critic prompt + improve_fn."""
    return reflect(
        initial_output=initial_result,
        context=diff_text,
        critic_system_prompt=REVIEW_CRITIC_PROMPT,
        improve_fn=_apply_review_critique,
        iterations=iterations,
    )
