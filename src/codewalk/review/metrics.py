"""Review metrics and observability helpers."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from src.codewalk.review.report import ReviewReport


@dataclass
class ReviewMetrics:
    """Metrics captured for a single review run."""

    files_reviewed: int = 0
    findings_count: int = 0
    clusters_count: int = 0
    token_usage: int = 0
    token_cost_estimate: float = 0.0
    latency_seconds: float = 0.0
    critical_count: int = 0
    warning_count: int = 0
    suggestion_count: int = 0
    blocking_count: int = 0
    security_count: int = 0
    verified_count: int = 0
    false_positive_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_reviewed": self.files_reviewed,
            "findings_count": self.findings_count,
            "clusters_count": self.clusters_count,
            "token_usage": self.token_usage,
            "token_cost_estimate": self.token_cost_estimate,
            "latency_seconds": self.latency_seconds,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "suggestion_count": self.suggestion_count,
            "blocking_count": self.blocking_count,
            "security_count": self.security_count,
            "verified_count": self.verified_count,
            "false_positive_count": self.false_positive_count,
        }


def compute_metrics(report: ReviewReport) -> ReviewMetrics:
    """Compute metrics from a completed ReviewReport."""
    metrics = ReviewMetrics(
        files_reviewed=report.files_reviewed,
        findings_count=len(report.findings),
        clusters_count=len(report.clusters),
        token_usage=report.token_usage,
        latency_seconds=report.time_seconds,
    )

    for finding in report.findings:
        if finding.severity.value == "blocker":
            metrics.critical_count += 1
        elif finding.severity.value == "error":
            metrics.warning_count += 1
        elif finding.severity.value == "suggestion":
            metrics.suggestion_count += 1

        if finding.blocking:
            metrics.blocking_count += 1
        if finding.category.value == "security":
            metrics.security_count += 1
        if finding.source.value == "verification":
            metrics.verified_count += 1

    # Rough cost estimate: $5 per 1M tokens for GPT-4-class models.
    metrics.token_cost_estimate = (report.token_usage / 1_000_000) * 5.0

    return metrics


class ReviewTimer:
    """Simple context manager for timing review stages."""

    def __init__(self) -> None:
        self.start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> "ReviewTimer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        self.elapsed = time.perf_counter() - self.start
