"""Cross-file coherence synthesis pass for chunked code reviews.

After each changed file has been reviewed independently, this module runs a
second LLM pass that looks at the whole set of changed files together and flags
integration issues that span multiple files (signature mismatches, missing
imports/wiring, inconsistent handling, etc.).
"""
from __future__ import annotations

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log
from src.codewalk.review.models import Issue, Severity, Category, Verdict, Confidence
from src.codewalk.review.review_prompts import REVIEW_CROSS_FILE_PROMPT
from src.codewalk.review.schemas import CrossFileReviewOutputSchema


_CATEGORY_MAP: dict[str, Category] = {
    "bug": Category.BUG,
    "security": Category.SECURITY,
    "style": Category.STYLE,
    "test": Category.TEST,
    "blast_radius": Category.BLAST_RADIUS,
    "design": Category.DESIGN,
    "naming": Category.NAMING,
    "complexity": Category.COMPLEXITY,
    "error_handling": Category.ERROR_HANDLING,
    "type_safety": Category.TYPE_SAFETY,
    "architecture": Category.ARCHITECTURE,
    "logging": Category.LOGGING,
    "compatibility": Category.COMPATIBILITY,
    "privacy": Category.PRIVACY,
    "hygiene": Category.HYGIENE,
}

_CONFIDENCE_MAP: dict[str, Confidence] = {
    "high": Confidence.HIGH,
    "medium": Confidence.MEDIUM,
    "low": Confidence.LOW,
}


def _format_file_summary(file_ctx) -> str:
    """Build a concise summary of one changed file for the synthesis prompt."""
    df = file_ctx.diff_file
    lines = [
        f"### {df.file_path}",
        f"- Language: {df.language}",
        f"- Added lines: {df.added_lines}, removed lines: {df.removed_lines}",
    ]
    if file_ctx.caller_context:
        callers = file_ctx.caller_context[:800]
        lines.append(f"- Callers/importers:\n{callers}")
    return "\n".join(lines)


def _format_per_file_issues(issues: list[Issue]) -> str:
    """Summarize the top issues from each file for the synthesis prompt."""
    if not issues:
        return "No per-file issues found."

    grouped: dict[str, list[Issue]] = {}
    for issue in issues:
        grouped.setdefault(issue.file_path, []).append(issue)

    lines = []
    for file_path, file_issues in grouped.items():
        lines.append(f"#### {file_path}")
        for issue in file_issues[:5]:  # top 5 per file to keep prompt size sane
            loc = f":{issue.line_number}" if issue.line_number else ""
            lines.append(
                f"- [{issue.severity.value}] {issue.category.value}{loc} — {issue.title}"
            )
        lines.append("")
    return "\n".join(lines)


def synthesize_cross_file_issues(ctx, per_file_issues: list[Issue]) -> list[Issue]:
    """Run a cross-file coherence pass and return any new integration issues.

    Args:
        ctx: ReviewContext with diff files, blast radius warnings, and file contexts.
        per_file_issues: Issues already found by per-file reviews.

    Returns:
        A list of new Issue objects tagged as cross-file findings. Empty list if
        the LLM finds no integration issues or if the call fails.
    """
    # Only run when there are multiple changed files — single-file diffs have no
    # cross-file concerns.
    if len(ctx.diff_files) < 2:
        return []

    file_summaries = "\n\n".join(
        _format_file_summary(fc) for fc in ctx.file_contexts
    )

    blast_radius = "\n".join(ctx.blast_radius_warnings) or "None."

    user_content = (
        "## Changed files summary\n\n"
        f"{file_summaries}\n\n"
        "## Per-file review findings (already reported; do NOT repeat)\n\n"
        f"{_format_per_file_issues(per_file_issues)}\n\n"
        "## Blast radius warnings\n\n"
        f"{blast_radius}\n\n"
        "## Diff\n\n"
        f"```\n{ctx.diff_text[:8000]}\n```"
    )

    try:
        llm = get_llm(temperature=0)
        structured = llm.with_structured_output(
            CrossFileReviewOutputSchema, method="json_mode"
        )
        response = structured.invoke([
            {"role": "system", "content": REVIEW_CROSS_FILE_PROMPT},
            {"role": "user", "content": user_content},
        ])
    except Exception as e:
        _log(f"[cross_file] structured output failed: {e}")
        return []

    issues = []
    for schema_issue in (response.issues or []):
        try:
            issue = Issue(
                severity=Severity[schema_issue.severity.upper()],
                category=_CATEGORY_MAP.get(schema_issue.category.lower(), Category.ARCHITECTURE),
                file_path=schema_issue.file or "unknown",
                line_number=schema_issue.line,
                title=schema_issue.title or "",
                explanation=schema_issue.explanation or "",
                confidence=_CONFIDENCE_MAP.get(schema_issue.confidence, Confidence.MEDIUM),
                suggestion=schema_issue.suggestion,
                fix_description=schema_issue.fix_description,
                code_snippet=schema_issue.code_snippet,
                cross_file=True,
            )
            issues.append(issue)
        except Exception as e:
            _log(f"[cross_file] skipping malformed issue: {e}")
            continue

    return issues


def upgrade_verdict_for_cross_file_issues(
    current_verdict: Verdict,
    cross_file_issues: list[Issue],
) -> tuple[Verdict, str]:
    """Upgrade verdict when cross-file synthesis finds breaking/integration issues."""
    if not cross_file_issues:
        return current_verdict, ""

    has_critical = any(
        i.severity == Severity.CRITICAL and i.confidence == Confidence.HIGH
        for i in cross_file_issues
    )
    if has_critical and current_verdict != Verdict.REQUEST_CHANGES:
        return (
            Verdict.REQUEST_CHANGES,
            "Cross-file synthesis detected critical integration issues.",
        )

    has_warning = any(i.severity == Severity.WARNING for i in cross_file_issues)
    if has_warning and current_verdict == Verdict.APPROVE:
        return (
            Verdict.APPROVE_WITH_NITS,
            "Cross-file synthesis detected integration concerns.",
        )

    return current_verdict, ""
