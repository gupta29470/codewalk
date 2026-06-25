"""Deduplication stage of the review pipeline."""
from __future__ import annotations

from src.codewalk.review.report import Confidence, Finding, Severity


def _severity_rank(severity: Severity) -> int:
    return {"blocker": 3, "error": 2, "suggestion": 1}.get(severity.value, 0)


def _confidence_rank(confidence: Confidence) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(confidence.value, 0)


def _pick_better(a: Finding, b: Finding) -> Finding:
    """Return the more important of two duplicate findings."""
    a_sev = _severity_rank(a.severity)
    b_sev = _severity_rank(b.severity)
    if a_sev != b_sev:
        return a if a_sev > b_sev else b

    a_conf = _confidence_rank(a.confidence)
    b_conf = _confidence_rank(b.confidence)
    if a_conf != b_conf:
        return a if a_conf > b_conf else b

    # Prefer the one with evidence.
    if bool(a.evidence) and not bool(b.evidence):
        return a
    if bool(b.evidence) and not bool(a.evidence):
        return b

    return a


def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Deduplicate findings by stable ID, keeping the highest impact copy.

    Args:
        findings: Raw findings from one or more review batches.

    Returns:
        Deduplicated list of findings.
    """
    by_id: dict[str, Finding] = {}
    for finding in findings:
        existing = by_id.get(finding.id)
        if existing is None:
            by_id[finding.id] = finding
        else:
            by_id[finding.id] = _pick_better(existing, finding)

    return list(by_id.values())
