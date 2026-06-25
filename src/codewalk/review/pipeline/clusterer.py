"""Clustering stage of the review pipeline."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from src.codewalk.review.report import Cluster, Finding, Severity


def _normalize_title(title: str) -> str:
    """Normalize a finding title for grouping."""
    lowered = title.lower()
    # Strip line numbers and file names that vary across occurrences.
    lowered = re.sub(r"\d+", "", lowered)
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return " ".join(lowered.split())


def _cluster_key(finding: Finding) -> str:
    return f"{finding.category.value}:{_normalize_title(finding.title)}"


def _severity_rank(severity: Severity) -> int:
    return {"blocker": 3, "error": 2, "suggestion": 1}.get(severity.value, 0)


def _representative(findings: list[Finding]) -> Finding:
    """Pick the representative finding (highest severity, then highest confidence)."""
    return max(
        findings,
        key=lambda f: (
            _severity_rank(f.severity),
            {"high": 3, "medium": 2, "low": 1}.get(f.confidence.value, 0),
            bool(f.current_code),
        ),
    )


def _cluster_severity(findings: list[Finding]) -> Severity:
    """Return the highest severity in the cluster."""
    return max(findings, key=lambda f: _severity_rank(f.severity)).severity


def cluster(findings: list[Finding]) -> list[Cluster]:
    """Group related findings into clusters.

    Args:
        findings: Verified findings.

    Returns:
        List of clusters. Each cluster contains a representative finding and
        all member findings.
    """
    groups: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        groups[_cluster_key(finding)].append(finding)

    clusters: list[Cluster] = []
    for key, members in groups.items():
        rep = _representative(members)
        title = rep.title
        cluster_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

        for member in members:
            member.cluster_id = cluster_id

        clusters.append(
            Cluster(
                id=cluster_id,
                title=title,
                representative_finding=rep,
                findings=members,
                severity=_cluster_severity(members),
                priority="P2" if _cluster_severity(members).value == "blocker" else "P3",
                count=len(members),
            )
        )

    return clusters
