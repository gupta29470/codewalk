"""Finding-centric review pipeline.

Stages:
    deduplicate → verify → cluster → rank → summarize → verdict
"""
from __future__ import annotations

from src.codewalk.review.pipeline.clusterer import cluster
from src.codewalk.review.pipeline.deduplicator import deduplicate
from src.codewalk.review.pipeline.ranker import rank
from src.codewalk.review.pipeline.summary_writer import write_narrative_summary, write_summary
from src.codewalk.review.pipeline.verdict_computer import compute_verdict
from src.codewalk.review.pipeline.verifier import verify

__all__ = [
    "cluster",
    "compute_verdict",
    "deduplicate",
    "rank",
    "verify",
    "write_summary",
    "write_narrative_summary",
]
