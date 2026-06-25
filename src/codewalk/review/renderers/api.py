"""API response renderer for review reports."""
from __future__ import annotations

from typing import Any

from src.codewalk.review.report import ReviewReport


def render_api_response(report: ReviewReport) -> dict[str, Any]:
    """Build the API response dict from a ReviewReport."""
    issues = [
        {
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
            "evidence": finding.evidence,
            "cluster_id": finding.cluster_id,
            "verifier_notes": finding.verifier_notes,
            "status": finding.status,
        }
        for finding in report.findings
    ]

    return {
        "schema_version": report.schema_version,
        "verdict": report.verdict.value,
        "verdict_reason": report.verdict_reason,
        "issues": issues,
        "summary": report.executive_summary,
        "narrative_summary": report.narrative_summary,
        "files_reviewed": report.files_reviewed,
        "lines_added": report.lines_added,
        "lines_removed": report.lines_removed,
        "session_id": report.session_id,
        "architecture_flags": report.architecture_flags.to_dict() if report.architecture_flags else None,
    }
