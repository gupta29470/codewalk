"""Verdict computation stage of the review pipeline."""
from __future__ import annotations

from src.codewalk.review.report import Cluster, Finding, Verdict
from src.codewalk.review.verdict import (
    compute_merge_blockers as _compute_merge_blockers,
    compute_verdict as _compute_verdict,
)


def compute_verdict(clusters: list[Cluster]) -> tuple[Verdict, str, list[str]]:
    """Compute the final verdict from ranked clusters.

    Args:
        clusters: Ranked clusters.

    Returns:
        Tuple of (verdict, verdict_reason, merge_blockers).
    """
    # Flatten clusters back into findings for the existing verdict logic.
    findings: list[Finding] = []
    for cluster in clusters:
        findings.extend(cluster.findings)

    verdict, reason = _compute_verdict(findings)
    blockers = _compute_merge_blockers(findings)
    return verdict, reason, blockers
