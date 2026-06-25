"""Ranking stage of the review pipeline."""
from __future__ import annotations

from src.codewalk.review.report import Category, Cluster, Confidence, Severity


# Hardcoded weights for Phase 2. Tune with evaluation data later.
_SEVERITY_WEIGHT = {
    "blocker": 10.0,
    "error": 3.0,
    "suggestion": 1.0,
}

_CONFIDENCE_WEIGHT = {
    "high": 1.5,
    "medium": 1.0,
    "low": 0.7,
}

_CATEGORY_WEIGHT = {
    "security": 5.0,
    "bug": 3.0,
    "error_handling": 2.5,
    "architecture": 2.0,
    "type_safety": 1.5,
    "blast_radius": 1.5,
    "test": 1.0,
    "complexity": 0.8,
    "logging": 0.7,
    "privacy": 1.5,
    "style": 0.3,
    "design": 0.5,
    "naming": 0.2,
    "hygiene": 0.2,
}


def _score(cluster: Cluster) -> float:
    """Compute a ranking score for a cluster."""
    rep = cluster.representative_finding

    severity = _SEVERITY_WEIGHT.get(rep.severity.value, 1.0)
    confidence = _CONFIDENCE_WEIGHT.get(rep.confidence.value, 1.0)
    category = _CATEGORY_WEIGHT.get(rep.category.value, 1.0)

    blocking_bonus = 2.0 if rep.blocking else 1.0
    frequency_bonus = 1.0 + min(cluster.count - 1, 4) * 0.2  # cap at +0.8

    return severity * confidence * category * blocking_bonus * frequency_bonus


def rank(clusters: list[Cluster]) -> list[Cluster]:
    """Sort clusters by impact score, highest first.

    Args:
        clusters: Clustered findings.

    Returns:
        Clusters sorted by descending score.
    """
    return sorted(clusters, key=lambda c: (_score(c), _SEVERITY_WEIGHT.get(c.severity.value, 0)), reverse=True)
