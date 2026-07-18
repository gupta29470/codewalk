"""API response renderer for review reports."""
from __future__ import annotations

from typing import Any

from src.codewalk.review.report import ReviewReport


def _issue_dict(finding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "severity": finding.severity.value,
        "confidence": finding.confidence.value,
        "category": finding.category.value,
        "file_path": finding.file_path,
        "line_number": finding.line_number,
        "title": finding.title,
        "explanation": finding.explanation,
        "suggestion": finding.recommended_code,
        "fix_description": finding.recommended_code,
        "code_snippet": finding.current_code,
        "blocking": finding.blocking,
        "source": finding.source.value,
        "status": finding.status,
    }


def render_api_response(report: ReviewReport) -> dict[str, Any]:
    """Build the API response dict from a ReviewReport.

    Issues come from the LLM; static_issues come from deterministic/static analysis.
    This mirrors the MCP split between ``findings`` and ``deterministic_findings``.
    """
    issues = [_issue_dict(f) for f in report.findings]
    static_issues = [_issue_dict(f) for f in report.deterministic_findings]

    return {
        "schema_version": report.schema_version,
        "issues": issues,
        "static_issues": static_issues,
        "files_reviewed": report.files_reviewed,
        "lines_added": report.lines_added,
        "lines_removed": report.lines_removed,
        "session_id": report.session_id,
        "architecture_flags": report.architecture_flags.to_dict() if report.architecture_flags else None,
    }
