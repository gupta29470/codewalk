"""CLI renderer for review reports."""
from __future__ import annotations

from src.codewalk.review.report import ReviewReport


def render_cli(report: ReviewReport) -> str:
    """Render a ReviewReport as terminal-friendly text."""
    lines = [
        f"Verdict: {report.verdict.value}",
        f"Reason: {report.verdict_reason}",
        "",
        f"Summary: {report.executive_summary}",
        "",
        f"Files reviewed: {report.files_reviewed} (+{report.lines_added}/-{report.lines_removed})",
        "",
    ]

    if report.merge_blockers:
        lines.extend(["Merge blockers:", ""])
        for blocker in report.merge_blockers:
            lines.append(f"  - {blocker}")
        lines.append("")

    if report.findings:
        lines.extend(["Findings:", ""])
        for finding in report.findings:
            loc = f"{finding.file_path}:{finding.line_number}" if finding.line_number else finding.file_path
            lines.append(
                f"  [{finding.severity.value}] {loc} — {finding.title}"
            )
            if finding.explanation:
                lines.append(f"    {finding.explanation}")
            lines.append("")

    return "\n".join(lines)
